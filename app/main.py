"""
TranscribeAI — Render orchestrator.

This service does NOT run Whisper. It:
1. Authenticates the user via their Supabase session token, and checks
   their plan's remaining quota.
2. Reads Alaa's current tunnel URL from Supabase.
3. Pings it to fail fast if his machine/tunnel is offline.
4. Downloads the video's audio via yt-dlp.
5. Submits the audio to his local Whisper API (which replies immediately
   with a job_id) and polls /status/{job_id} until done — this keeps every
   single HTTP call through the Cloudflare tunnel short, so the tunnel
   never times out even on long videos.
6. Returns the transcript, and updates the user's quota usage.
"""
import asyncio
import httpx
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from .config import settings, PLAN_LIMITS_MINUTES
from .tunnel import get_current_tunnel_url, check_whisper_alive, TunnelUnavailableError
from .downloader import download_audio, detect_platform, cleanup_job_dir, DownloadError
from .auth import get_current_user, get_profile, update_minutes_used

app = FastAPI(title="TranscribeAI Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten once the frontend domain is fixed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranscribeRequest(BaseModel):
    url: HttpUrl
    language: str = "auto"


POLL_INTERVAL_SECONDS = 4
MAX_POLL_ATTEMPTS = 150  # 150 * 4s = 10 minutes max wait for the transcription itself


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/whisper-status")
async def whisper_status():
    """Lets the frontend show a live green/red availability indicator. No auth needed."""
    try:
        tunnel_url = await get_current_tunnel_url()
    except TunnelUnavailableError as e:
        return {"available": False, "reason": str(e)}

    alive = await check_whisper_alive(tunnel_url)
    return {"available": alive, "reason": None if alive else "Whisper machine is offline."}


@app.get("/api/me")
async def me(user=Depends(get_current_user)):
    profile = await get_profile(user["id"])
    limit = PLAN_LIMITS_MINUTES.get(profile.get("plan", "free"), 30)
    return {
        "email": profile.get("email"),
        "plan": profile.get("plan", "free"),
        "minutes_used_this_period": profile.get("minutes_used_this_period", 0),
        "minutes_limit": limit,
    }


@app.post("/api/transcribe")
async def transcribe(payload: TranscribeRequest, user=Depends(get_current_user)):
    # 1. Load profile and enforce quota
    profile = await get_profile(user["id"])
    plan = profile.get("plan", "free")
    minutes_used = profile.get("minutes_used_this_period", 0)
    minutes_limit = PLAN_LIMITS_MINUTES.get(plan, 30)

    if minutes_used >= minutes_limit:
        raise HTTPException(
            status_code=402,
            detail=f"You've used your {minutes_limit} min/month on the {plan} plan. Upgrade to continue.",
        )

    # 2. Fail fast if Alaa's machine isn't reachable — don't waste time downloading first.
    try:
        tunnel_url = await get_current_tunnel_url()
    except TunnelUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="Transcription is temporarily unavailable. Please try again shortly.",
        )

    alive = await check_whisper_alive(tunnel_url)
    if not alive:
        raise HTTPException(
            status_code=503,
            detail="Transcription is temporarily unavailable. Please try again shortly.",
        )

    # 3. Download audio — cap this job at whatever quota the user has left
    url = str(payload.url)
    platform = detect_platform(url)
    job_dir = None
    remaining_minutes = minutes_limit - minutes_used
    max_seconds = min(settings.max_job_seconds, int(remaining_minutes * 60))

    try:
        download = download_audio(url, max_duration_seconds=max_seconds)
        job_dir = download["job_dir"]

        # 4. Submit to local Whisper — returns immediately with a job_id.
        with open(download["wav_path"], "rb") as f:
            files = {"file": ("audio.wav", f, "audio/wav")}
            data = {"language": payload.language}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{tunnel_url}/transcribe", files=files, data=data)

        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Transcription engine returned an error ({resp.status_code}).",
            )

        submit_result = resp.json()
        job_id = submit_result.get("job_id")
        if not job_id:
            raise HTTPException(status_code=502, detail="Transcription engine did not return a job id.")

        # 5. Poll /status/{job_id} with short requests until done.
        result = None
        async with httpx.AsyncClient(timeout=15) as client:
            for _ in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    status_resp = await client.get(f"{tunnel_url}/status/{job_id}")
                except httpx.RequestError:
                    continue

                if status_resp.status_code != 200:
                    continue

                status_data = status_resp.json()
                if status_data.get("status") == "done":
                    result = status_data
                    break
                elif status_data.get("status") == "error":
                    raise HTTPException(
                        status_code=502,
                        detail=f"Transcription failed: {status_data.get('error', 'unknown error')}",
                    )

        if result is None:
            raise HTTPException(
                status_code=504,
                detail="Transcription is taking longer than expected. Please try again.",
            )

        # 6. Update quota usage
        actual_duration = result.get("duration") or download.get("duration_seconds") or 0
        new_total = minutes_used + (actual_duration / 60.0)
        await update_minutes_used(user["id"], new_total)

        return {
            "transcript": result["text"],
            "detected_language": result.get("detected_language"),
            "platform": platform,
            "duration_seconds": actual_duration,
            "minutes_used_this_period": round(new_total, 2),
            "minutes_limit": minutes_limit,
        }

    except DownloadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Lost connection to the transcription engine. Please try again.",
        )
    finally:
        if job_dir:
            cleanup_job_dir(job_dir)
