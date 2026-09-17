"""
Stripe billing.

Two endpoints:
- POST /api/billing/create-checkout-session — logged-in user picks a plan,
  gets redirected to Stripe's hosted checkout page.
- POST /api/billing/webhook — Stripe calls this after a successful payment
  (or cancellation) to tell us to update the user's plan in Supabase.

Until real Stripe keys are set (see config.py), create-checkout-session
returns a clear 501 error instead of crashing, so the rest of the app
keeps working normally.
"""
import stripe
import httpx
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from .config import settings
from .auth import get_current_user, get_profile

router = APIRouter(prefix="/api/billing", tags=["billing"])

stripe.api_key = settings.stripe_secret_key

PRICE_IDS = {
    "creator": settings.stripe_price_creator,
    "pro": settings.stripe_price_pro,
}

PLACEHOLDER_MARKERS = ("placeholder",)


def _stripe_configured() -> bool:
    return not any(m in settings.stripe_secret_key for m in PLACEHOLDER_MARKERS)


class CheckoutRequest(BaseModel):
    plan: str  # "creator" or "pro"


async def _update_profile_stripe_customer(user_id: str, customer_id: str):
    async with httpx.AsyncClient(timeout=10) as client:
        await client.patch(
            f"{settings.supabase_url}/rest/v1/profiles",
            params={"id": f"eq.{user_id}"},
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={"stripe_customer_id": customer_id},
        )


async def _set_plan_by_customer_id(customer_id: str, plan: str, reset_usage: bool = True):
    payload = {"plan": plan}
    if reset_usage:
        payload["minutes_used_this_period"] = 0
    async with httpx.AsyncClient(timeout=10) as client:
        await client.patch(
            f"{settings.supabase_url}/rest/v1/profiles",
            params={"stripe_customer_id": f"eq.{customer_id}"},
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=payload,
        )


@router.post("/create-checkout-session")
async def create_checkout_session(payload: CheckoutRequest, user=Depends(get_current_user)):
    if not _stripe_configured():
        raise HTTPException(
            status_code=501,
            detail="Payments are not configured yet. Please try again later.",
        )

    if payload.plan not in PRICE_IDS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    profile = await get_profile(user["id"])
    customer_id = profile.get("stripe_customer_id")

    if not customer_id:
        customer = stripe.Customer.create(email=user["email"], metadata={"supabase_user_id": user["id"]})
        customer_id = customer.id
        await _update_profile_stripe_customer(user["id"], customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": PRICE_IDS[payload.plan], "quantity": 1}],
        success_url=f"{settings.frontend_url}/?checkout=success",
        cancel_url=f"{settings.frontend_url}/?checkout=cancelled",
        metadata={"supabase_user_id": user["id"], "plan": payload.plan},
    )
    return {"checkout_url": session.url}


@router.post("/create-portal-session")
async def create_portal_session(user=Depends(get_current_user)):
    if not _stripe_configured():
        raise HTTPException(status_code=501, detail="Payments are not configured yet.")

    profile = await get_profile(user["id"])
    customer_id = profile.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{settings.frontend_url}/",
    )
    return {"portal_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    if not _stripe_configured():
        raise HTTPException(status_code=501, detail="Payments are not configured yet.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        user_id = data["metadata"].get("supabase_user_id")
        plan = data["metadata"].get("plan")
        if user_id and plan:
            # Direct update by user id here since we have it from metadata.
            async with httpx.AsyncClient(timeout=10) as client:
                await client.patch(
                    f"{settings.supabase_url}/rest/v1/profiles",
                    params={"id": f"eq.{user_id}"},
                    headers={
                        "apikey": settings.supabase_service_key,
                        "Authorization": f"Bearer {settings.supabase_service_key}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    json={"plan": plan, "minutes_used_this_period": 0},
                )

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id:
            await _set_plan_by_customer_id(customer_id, "free", reset_usage=False)

    elif event_type == "invoice.paid":
        # Monthly renewal — reset usage counter for the existing plan.
        customer_id = data.get("customer")
        if customer_id:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.patch(
                    f"{settings.supabase_url}/rest/v1/profiles",
                    params={"stripe_customer_id": f"eq.{customer_id}"},
                    headers={
                        "apikey": settings.supabase_service_key,
                        "Authorization": f"Bearer {settings.supabase_service_key}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    json={"minutes_used_this_period": 0},
                )

    return {"status": "ok"}
