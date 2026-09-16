from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase — used here just to read/write the tunnel_url row.
    # Full auth/profiles wiring comes in a later step.
    supabase_url: str
    supabase_service_key: str

    max_job_seconds: int = 900  # 15 min hard cap per video for the MVP
    whisper_call_timeout: int = 600  # generous — long videos take time to transcribe

    class Config:
        env_file = ".env"


settings = Settings()
