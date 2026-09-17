"""
Verifies the Supabase access token sent by the frontend and fetches the
matching profile row (plan, quota usage) using the service_role key —
this bypasses RLS safely, since the request has already been authenticated.
"""
import httpx
from fastapi import Header, HTTPException
from .config import settings


class AuthError(Exception):
    pass


async def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI dependency: expects 'Authorization: Bearer <supabase_access_token>'."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.split(" ", 1)[1]

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {token}",
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")

    return resp.json()  # contains at least: id, email


async def get_profile(user_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.supabase_url}/rest/v1/profiles",
            params={"id": f"eq.{user_id}", "select": "*"},
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail="Could not load user profile.")

    rows = resp.json()
    if not rows:
        raise HTTPException(status_code=404, detail="Profile not found.")

    return rows[0]


async def update_minutes_used(user_id: str, new_total_minutes: float):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{settings.supabase_url}/rest/v1/profiles",
            params={"id": f"eq.{user_id}"},
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={"minutes_used_this_period": round(new_total_minutes, 2)},
        )
    if resp.status_code not in (200, 204):
        # Don't fail the whole request over a quota-tracking write — log-worthy,
        # but the user already has their transcript at this point.
        pass
