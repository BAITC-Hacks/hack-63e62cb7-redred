import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_origin: str
    secure_cookie: bool
    session_hours: int
    currency: str
    ekt_api_base_url: str
    ekt_api_username: str
    ekt_api_password: str
    ekt_api_timeout_seconds: float
    upload_dir: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+asyncpg://hackalem:hackalem@127.0.0.1:5432/hackalem"),
        app_origin=os.getenv("APP_ORIGIN", "http://localhost:5173").rstrip("/"),
        secure_cookie=os.getenv("SECURE_COOKIE", "false").lower() in ("1", "true", "yes"),
        session_hours=int(os.getenv("SESSION_HOURS", "24")),
        currency=os.getenv("CURRENCY", "KZT"),
        ekt_api_base_url=os.getenv("EKT_API_BASE_URL", "https://ekt.kz/api").rstrip("/"),
        ekt_api_username=os.getenv("EKT_API_USERNAME", ""),
        ekt_api_password=os.getenv("EKT_API_PASSWORD", ""),
        ekt_api_timeout_seconds=float(os.getenv("EKT_API_TIMEOUT_SECONDS", "2.5")),
        upload_dir=os.getenv("UPLOAD_DIR", "backend/uploads"),
    )
