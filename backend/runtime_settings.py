"""Durable, allowlisted operator settings for catalog synchronization."""

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .errors import APIError
from .models import RuntimeSetting, utcnow


KEYS = (
    "catalog_sync_enabled",
    "catalog_sync_interval_seconds",
    "catalog_sync_concurrency",
    "catalog_sync_timeout_seconds",
)


def _validate(key: str, value: Any) -> bool | int | float:
    if key == "catalog_sync_enabled":
        if type(value) is bool:
            return value
    elif key == "catalog_sync_interval_seconds":
        if type(value) is int and 60 <= value <= 86400:
            return value
    elif key == "catalog_sync_concurrency":
        if type(value) is int and 1 <= value <= 2:
            return value
    elif key == "catalog_sync_timeout_seconds":
        if type(value) in (int, float) and math.isfinite(value) and 1 <= value <= 60:
            return float(value)
    raise ValueError(f"invalid value for {key}")


async def read_settings(db: AsyncSession, defaults: Settings | None = None) -> dict[str, bool | int | float]:
    defaults = defaults or get_settings()
    values: dict[str, bool | int | float] = {key: getattr(defaults, key) for key in KEYS}
    rows = (await db.execute(select(RuntimeSetting.key, RuntimeSetting.value).where(RuntimeSetting.key.in_(KEYS)))).all()
    for key, payload in rows:
        try:
            values[key] = _validate(key, payload["value"])
        except (ValueError, KeyError, TypeError):
            # A malformed stored value cannot enable unsafe runtime settings.
            continue
    return values


async def update_settings(db: AsyncSession, patch: dict[str, Any]) -> dict[str, bool | int | float]:
    if not isinstance(patch, dict) or not patch or set(patch) - set(KEYS):
        raise APIError(422, "INVALID_RUNTIME_SETTINGS", "Недопустимые параметры настроек")
    try:
        validated = {key: _validate(key, value) for key, value in patch.items()}
    except ValueError:
        raise APIError(422, "INVALID_RUNTIME_SETTINGS", "Недопустимые значения настроек") from None
    for key, value in validated.items():
        statement = pg_insert(RuntimeSetting).values(key=key, value={"value": value}, updated_at=utcnow())
        await db.execute(statement.on_conflict_do_update(
            index_elements=[RuntimeSetting.key],
            set_={"value": statement.excluded.value, "updated_at": statement.excluded.updated_at},
        ))
    await db.commit()
    return await read_settings(db)
