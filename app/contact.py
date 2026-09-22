"""
Contact form endpoint.

Uses Resend (free tier: 3000 emails/month, no credit card) to:
1. Notify Alaa at his email with the visitor's message.
2. Send the visitor a short thank-you confirmation.

Sends from a verified transcribeai.site address — required for delivering
to arbitrary visitor emails (Resend's shared sandbox address only allows
sending to the account owner's own verified email).
"""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from .config import settings

router = APIRouter(prefix="/api", tags=["contact"])

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "TranscribeAI <contact@transcribeai.site>"


def _resend_configured() -> bool:
    return bool(settings.resend_api_key)


class ContactRequest(BaseModel):
    name: str
    email: EmailStr
    message: str


async def _send_email(to: str, subject: str, html: str):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={"from": FROM_ADDRESS, "to": [to], "subject": subject, "html": html},
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Resend error ({resp.status_code}): {resp.text[:200]}")


@router.post("/contact")
async def contact(payload: ContactRequest):
    if not _resend_configured():
        raise HTTPException(status_code=501, detail="Contact form is not configured yet.")

    name = payload.name.strip()
    email = payload.email
    message = payload.message.strip()

    if not name or not message:
        raise HTTPException(status_code=400, detail="Name and message are required.")

    # 1. Notify Alaa
    notify_html = f"""
        <h2>New message from TranscribeAI</h2>
        <p><b>Name:</b> {name}</p>
        <p><b>Email:</b> {email}</p>
        <p><b>Message:</b></p>
        <p>{message}</p>
    """
    try:
        await _send_email(settings.contact_notify_email, f"New contact from {name}", notify_html)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # 2. Thank the visitor — best-effort, don't fail the request if this
    # particular send has an issue (Alaa already got the notification).
    thanks_html = f"""
        <p>Hi {name},</p>
        <p>Thanks for reaching out to TranscribeAI — we've received your message
        and will get back to you shortly.</p>
        <p>— The TranscribeAI team</p>
    """
    try:
        await _send_email(email, "Thanks for contacting TranscribeAI", thanks_html)
    except RuntimeError as e:
        # Best-effort: Alaa already got the notification above, so don't
        # fail the whole request — but this shouldn't happen now that the
        # domain is verified, so print it for visibility in Render logs.
        print(f"[contact] Thank-you email failed: {e}")

    return {"status": "ok"}
