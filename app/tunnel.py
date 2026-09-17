"""
Reads the current Whisper tunnel URL from the tunnel_config table in Supabase.
This is how the Render-hosted API finds Alaa's local machine, wherever its
Cloudflare quick-tunnel URL currently points (it changes each time the
tunnel is restarted).
"""
import httpx
from .config import settings


class TunnelUnavailableError(Exception):
    pass


async def get_current_tunnel_url() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{settings.supabase_url}/rest/v1/tunnel_config",
                params={"id": "eq.1", "select": "tunnel_url,updated_at"},
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )
        except httpx.RequestError as e:
            raise TunnelUnavailableError(f"Could not reach Supabase: {e}")

    if resp.status_code != 200:
        raise TunnelUnavailableError(f"Supabase error: {resp.status_code} {resp.text}")

    rows = resp.json()
    if not rows or not rows[0].get("tunnel_url"):
        raise TunnelUnavailableError("No tunnel URL is currently registered.")

    return rows[0]["tunnel_url"]


async def check_whisper_alive(tunnel_url: str) -> bool:
    """Quick health ping before committing to a download — fail fast and clearly
    if Alaa's machine/tunnel is offline, rather than timing out after minutes."""
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"{tunnel_url}/health")
            return resp.status_code == 200
        except httpx.RequestError:
            return False
