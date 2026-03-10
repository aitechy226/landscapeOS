"""
Application configuration — all settings from environment variables.
Never hardcode secrets. Copy .env.example to .env for local dev.
"""
from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl, validator
from typing import Optional
import secrets


class Settings(BaseSettings):
    # ─── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "LandscapeOS"
    APP_ENV: str = "development"  # development | staging | production
    DEBUG: bool = False
    SECRET_KEY: str = secrets.token_urlsafe(32)
    API_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ─── Database (Supabase Postgres) ─────────────────────────────────────────
    DATABASE_URL: str  # postgresql+asyncpg://user:pass@host:6543/postgres
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # ─── Supabase ─────────────────────────────────────────────────────────────
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str  # service role key — never expose to frontend
    SUPABASE_ANON_KEY: str
    SUPABASE_JWT_SECRET: str   # from Supabase dashboard → Settings → API

    # ─── Anthropic ────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str

    # ─── Stripe ───────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str
    STRIPE_STARTER_PRICE_ID: str = ""
    STRIPE_PRO_PRICE_ID: str = ""
    STRIPE_ENTERPRISE_PRICE_ID: str = ""

    # ─── Twilio (SMS) ─────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""

    # ─── Resend (Email) ───────────────────────────────────────────────────────
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "noreply@landscapeos.com"
    EMAIL_FROM_NAME: str = "LandscapeOS"

    # ─── Mapbox ───────────────────────────────────────────────────────────────
    MAPBOX_ACCESS_TOKEN: str = ""

    # ─── Security ─────────────────────────────────────────────────────────────
    FIELD_ENCRYPTION_KEY: str = ""  # Fernet key for sensitive field encryption
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ─── Rate Limiting ────────────────────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10  # stricter for auth endpoints

    # ─── Features ─────────────────────────────────────────────────────────────
    ENABLE_SIGNUP: bool = True
    REQUIRE_EMAIL_VERIFICATION: bool = True
    SUPERADMIN_KEY: str = ""  # X-Admin-Key for /admin/* routes

    @validator("APP_ENV")
    def validate_env(cls, v):
        assert v in ("development", "staging", "production"), f"Invalid APP_ENV: {v}"
        return v

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
