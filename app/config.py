from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase — used to read/write the tunnel_url row, and now also for
    # verifying user sessions and reading/writing their profile (plan, quota).
    supabase_url: str
    supabase_service_key: str

    # Stripe — leave placeholders until the real dashboard keys are available.
    # The app still starts fine with placeholders; billing endpoints will
    # just return a clear error until real keys are set.
    stripe_secret_key: str = "sk_test_placeholder"
    stripe_webhook_secret: str = "whsec_placeholder"
    stripe_price_creator: str = "price_placeholder_creator"
    stripe_price_pro: str = "price_placeholder_pro"
    frontend_url: str = "https://transcribe-u5sf.onrender.com"

    max_job_seconds: int = 900  # 15 min hard cap per video for the MVP
    whisper_call_timeout: int = 600  # generous — long videos take time to transcribe

    class Config:
        env_file = ".env"


settings = Settings()

PLAN_LIMITS_MINUTES = {
    "free": 30,
    "creator": 600,
    "pro": 1800,
}
