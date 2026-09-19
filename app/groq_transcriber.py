"""
Transcription via Groq's cloud-hosted Whisper (large-v3-turbo).

Free tier: ~2000 requests/day, 28800 audio-seconds/day (8 hours), no
credit card required. This replaces the previous local-Whisper-over-
Cloudflare-tunnel setup — transcription no longer depends on Alaa's
machine being on.
"""
import httpx
from .config import settings


class TranscriptionError(Exception):
    pass


GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


def groq_configured() -> bool:
    return bool(settings.groq_api_key)


async def check_groq_alive() -> bool:
    """Lightweight reachability check for the availability indicator."""
    if not groq_configured():
        return False
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
            return resp.status_code == 200
        except httpx.RequestError:
            return False


async def transcribe_audio(wav_path: str, language: str = "auto") -> dict:
    """
    Sends the audio file to Groq's Whisper endpoint.
    Returns dict with: text, detected_language, duration
    """
    if not groq_configured():
        raise TranscriptionError("Transcription is not configured yet.")

    data = {"model": GROQ_MODEL, "response_format": "verbose_json"}
    if language and language != "auto":
        data["language"] = language

    try:
        with open(wav_path, "rb") as f:
            files = {"file": ("audio.mp3", f, "audio/mpeg")}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    data=data,
                    files=files,
                )
    except httpx.RequestError as e:
        raise TranscriptionError(f"Could not reach the transcription service: {e}")

    if resp.status_code != 200:
        raise TranscriptionError(f"Transcription engine returned an error ({resp.status_code}): {resp.text[:200]}")

    result = resp.json()
    return {
        "text": result.get("text", "").strip(),
        "detected_language": result.get("language"),
        "duration": result.get("duration"),
    }
