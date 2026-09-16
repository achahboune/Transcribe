"""
TranscribeAI — Fly orchestrator.

This service does NOT run Whisper. It:
1. Reads Alaa's current tunnel URL from Supabase.
2. Pings it to fail fast if his machine/tunnel is offline.
3. Downloads the video's audio via yt-dlp.
4. Submits the audio to his local Whisper API (which replies immediately
   with a job_id) and polls /status/{job_id} until done — this keeps every
   single HTTP call through the Cloudflare tunnel short, so the tunnel
   never times out even on long videos.
5. Returns the transcript.

Auth (Supabase users) and Stripe billing are added in a later step —
this version proves the download -> tunnel -> whisper -> response path end to end.
"""
import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from .config import settings
from .tunnel import get_current_tunnel_url, check_whisper_alive, TunnelUnavailableError
from .downloader import download_audio, detect_platform, cleanup_job_dir, DownloadError

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
    """Lets the frontend show a live green/red availability indicator."""
    try:
        tunnel_url = await get_current_tunnel_url()
    except TunnelUnavailableError as e:
        return {"available": False, "reason": str(e)}

    alive = await check_whisper_alive(tunnel_url)
    return {"available": alive, "reason": None if alive else "Whisper machine is offline."}


@app.post("/api/transcribe")
async def transcribe(payload: TranscribeRequest):
    # 1. Fail fast if Alaa's machine isn't reachable — don't waste time downloading first.
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

    # 2. Download audio
    url = str(payload.url)
    platform = detect_platform(url)
    job_dir = None
    try:
        download = download_audio(url, max_duration_seconds=settings.max_job_seconds)
        job_dir = download["job_dir"]

        # 3. Submit to local Whisper — returns immediately with a job_id.
        # Short-lived call, so the tunnel never has time to time out here.
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

        # 4. Poll /status/{job_id} with short requests until done.
        # Each poll is its own brief HTTP call through the tunnel — never
        # one long-held connection — so long videos no longer trigger a
        # tunnel timeout (524).
        result = None
        async with httpx.AsyncClient(timeout=15) as client:
            for _ in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    status_resp = await client.get(f"{tunnel_url}/status/{job_id}")
                except httpx.RequestError:
                    # Transient blip on the tunnel — keep polling, don't fail immediately.
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
                # else: still "processing" — keep polling

        if result is None:
            raise HTTPException(
                status_code=504,
                detail="Transcription is taking longer than expected. Please try again.",
            )

        return {
            "transcript": result["text"],
            "detected_language": result.get("detected_language"),
            "platform": platform,
            "duration_seconds": result.get("duration") or download.get("duration_seconds"),
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
