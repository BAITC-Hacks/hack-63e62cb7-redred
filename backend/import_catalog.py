"""Load a small reproducible demo subset or a strictly bounded live EKT sample.

Run from the repository root: python -m backend.import_catalog --demo
"""

import argparse
import asyncio
import json
import ssl

import httpx

from .catalog import CatalogService, DATA_DIR
from .config import get_settings
from .db import get_session_factory


async def import_demo(service: CatalogService) -> int:
    fixtures = json.loads((DATA_DIR / "demo_products.json").read_text(encoding="utf-8"))
    for item in fixtures:
        # These are selected API fields from the September 23 audit, not a live stock check.
        await service.save_raw(item, "2026-09-23T00:00:00Z")
    return len(fixtures)


async def import_live(service: CatalogService, max_products: int) -> int:
    if not 1 <= max_products <= 500:
        raise ValueError("--max-products must be between 1 and 500")
    seen_ids: set[int] = set()
    seen_pages: set[tuple[int, ...]] = set()
    imported = 0
    for page_no in range(1, (max_products + 99) // 100 + 2):
        page = await service.ekt.page(page_no)
        page_ids = tuple(int(x["id"]) for x in page if isinstance(x, dict) and x.get("id"))
        if not page_ids or page_ids in seen_pages:
            break
        seen_pages.add(page_ids)
        new_ids = [pid for pid in page_ids if pid not in seen_ids]
        if not new_ids:
            break
        for pid in new_ids:
            seen_ids.add(pid)
            raw = await service.ekt.detail(pid)
            await service.save_raw(raw)
            imported += 1
            if imported >= max_products:
                return imported
        if len(page) < 100:
            break
    return imported


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import a bounded EKT sample into PostgreSQL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo", action="store_true", help="Load four audited demo cards")
    group.add_argument("--max-products", type=int, help="Fetch at most this many live cards, up to 500")
    args = parser.parse_args()
    async with httpx.AsyncClient(verify=ssl.create_default_context()) as client:
        service = CatalogService(get_session_factory(), client, get_settings())
        count = await (import_demo(service) if args.demo else import_live(service, args.max_products))
    print(f"Imported {count} product snapshots")


if __name__ == "__main__":
    asyncio.run(main())
