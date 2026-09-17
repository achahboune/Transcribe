from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase — used to read/write the tunnel_url row, and now also for
    # verifying user sessions and reading/writing their profile (plan, quota).
    supabase_url: str
    supabase_service_key: str

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
