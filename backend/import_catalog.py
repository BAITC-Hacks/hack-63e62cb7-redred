"""Load a demo subset, a bounded sample, or run the shared catalog synchronizer.

Run from the repository root: python -m backend.import_catalog --demo
"""

import argparse
import asyncio
import json
import ssl
from datetime import datetime, timezone

import httpx

from .catalog import CatalogService, DATA_DIR
from .config import get_settings
from .db import get_engine, get_session_factory


async def import_demo(service: CatalogService) -> int:
    fixtures = json.loads((DATA_DIR / "demo_products.json").read_text(encoding="utf-8"))
    for item in fixtures:
        # These are full cached API cards from the September 23 audit, not a live stock check.
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
            observed_at = datetime.now(timezone.utc)
            raw = await service.ekt.detail(pid)
            await service.save_raw(raw, checked_at=observed_at)
            imported += 1
            if imported >= max_products:
                return imported
        if len(page) < 100:
            break
    return imported


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import EKT cards into PostgreSQL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo", action="store_true", help="Load 21 audited demo cards")
    group.add_argument("--max-products", type=int, help="Fetch at most this many live cards, up to 500")
    group.add_argument("--sync", choices=("full", "existing"), help="Run the shared catalog synchronizer and wait")
    args = parser.parse_args()
    async with httpx.AsyncClient(verify=ssl.create_default_context()) as client:
        service = CatalogService(get_session_factory(), client, get_settings())
        if args.sync:
            from .catalog_sync import CatalogSynchronizer

            synchronizer = CatalogSynchronizer(get_engine(), get_session_factory(), service, get_settings())
            try:
                run = await synchronizer.trigger(mode=args.sync, trigger="manual")
                while run["status"] == "running":
                    await asyncio.sleep(0.2)
                    run = await synchronizer.get_run(run["id"])
                print(json.dumps(run, ensure_ascii=False))
            finally:
                await synchronizer.stop()
        else:
            count = await (import_demo(service) if args.demo else import_live(service, args.max_products))
            print(f"Imported {count} product snapshots")


if __name__ == "__main__":
    asyncio.run(main())
