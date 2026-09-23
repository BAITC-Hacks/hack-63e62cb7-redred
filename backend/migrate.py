"""Apply the initial PostgreSQL schema for the prototype.

Run from the repository root: python -m backend.migrate
"""

import asyncio

from sqlalchemy import text

from .db import get_engine
from .models import Base


async def migrate() -> None:
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"))
        await connection.execute(text("INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING"))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
