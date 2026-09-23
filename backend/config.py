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
    catalog_sync_enabled: bool = True
    catalog_sync_interval_seconds: int = 900
    catalog_sync_concurrency: int = 2
    catalog_sync_timeout_seconds: float = 10.0
    admin_password: str = ""
    admin_origin: str = "http://localhost:8000"


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


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
        catalog_sync_enabled=os.getenv("CATALOG_SYNC_ENABLED", "true").lower() in ("1", "true", "yes"),
        catalog_sync_interval_seconds=_bounded_int("CATALOG_SYNC_INTERVAL_SECONDS", 900, 10, 86400),
        catalog_sync_concurrency=_bounded_int("CATALOG_SYNC_CONCURRENCY", 2, 1, 2),
        catalog_sync_timeout_seconds=_bounded_float("CATALOG_SYNC_TIMEOUT_SECONDS", 10.0, 1.0, 60.0),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        admin_origin=os.getenv("ADMIN_ORIGIN", "http://localhost:8000").rstrip("/"),
    )
