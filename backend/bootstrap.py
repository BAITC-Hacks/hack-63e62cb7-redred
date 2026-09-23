"""Initialize the container schema and seed only an empty catalog."""

import asyncio

import httpx
from sqlalchemy import text

from .catalog import CatalogService
from .config import get_settings
from .db import get_engine, get_session_factory
from .import_catalog import import_demo
from .migrate import migrate


async def bootstrap() -> None:
    await migrate()
    engine = get_engine()
    try:
        async with engine.connect() as connection:
            populated = await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM product_snapshots)"))
        if not populated:
            async with httpx.AsyncClient() as client:
                count = await import_demo(CatalogService(get_session_factory(), client, get_settings()))
                print(f"Initialized catalog with {count} cached product snapshots", flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(bootstrap())
