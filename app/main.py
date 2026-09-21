"""
TranscribeAI — Render orchestrator.

1. Authenticates the user via their Supabase session token, and checks
   their plan's remaining quota.
2. Downloads the video's audio via yt-dlp.
3. Sends the audio to Groq's cloud-hosted Whisper (free tier) for
   transcription — no dependency on any local machine being on.
4. Returns the transcript, and updates the user's quota usage.
"""
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

from .config import settings, PLAN_LIMITS_MINUTES
from .downloader import download_audio, detect_platform, cleanup_job_dir, DownloadError
from .groq_transcriber import transcribe_audio, check_groq_alive, groq_configured, TranscriptionError
from .auth import get_current_user, get_profile, update_minutes_used
from .billing import router as billing_router
from .contact import router as contact_router

app = FastAPI(title="TranscribeAI Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(billing_router)
app.include_router(contact_router)

# Serve the frontend (frontend/index.html) at the root — this avoids the
# artifact/claude.ai CSP that blocks fetch() calls to external APIs like
# Supabase. Served from the same origin as the API, so no CORS issues either.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    @app.get("/")
    def serve_index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))


class TranscribeRequest(BaseModel):
    url: HttpUrl
    language: str = "auto"


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/whisper-status")
async def whisper_status():
    """Lets the frontend show a live green/red availability indicator. No auth needed."""
    if not groq_configured():
        return {"available": False, "reason": "Transcription is not configured yet."}
    alive = await check_groq_alive()
    return {"available": alive, "reason": None if alive else "Transcription service is temporarily unreachable."}


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

    if not groq_configured():
        raise HTTPException(
            status_code=503,
            detail="Transcription is temporarily unavailable. Please try again shortly.",
        )

    # 2. Download audio — cap this job at whatever quota the user has left
    url = str(payload.url)
    platform = detect_platform(url)
    job_dir = None
    remaining_minutes = minutes_limit - minutes_used
    max_seconds = min(settings.max_job_seconds, int(remaining_minutes * 60))

    try:
        download = download_audio(url, max_duration_seconds=max_seconds)
        job_dir = download["job_dir"]

        # 3. Transcribe via Groq
        result = await transcribe_audio(download["wav_path"], language=payload.language)

        # 4. Update quota usage
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
    except TranscriptionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        if job_dir:
            cleanup_job_dir(job_dir)
