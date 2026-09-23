"""Live HTTP chat verification using real OpenAI/EKT; run from the repository root.

Uses API credits. --prepare-db creates the schema and imports the four demo cards
in DATABASE_URL. Set that variable to a disposable test database first.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from ai.evaluate_hypotheses import load_local_env


async def verify(prepare: bool):
    from backend.main import app
    from backend.import_catalog import import_demo
    from backend.migrate import migrate
    from backend.db import get_session_factory
    from backend.models import Session
    from backend.sessions import COOKIE_NAME, resolve_session_from_cookie
    from sqlalchemy import delete

    if prepare:
        await migrate()
    results = []
    async with app.router.lifespan_context(app):
        if prepare:
            await import_demo(app.state.catalog)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://localhost:5173"}) as client:
            response = await client.post("/api/session", json={})
            response.raise_for_status()
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            try:
                for name, text in [
                    ("terms", "Какие способы оплаты, доставка и минимальная партия?"),
                    ("product", "Нужны 2 штуки артикула 010500006_. Покажи наличие, характеристики и сертификат и подготовь предложение."),
                    ("confirm", "да, добавь"),
                    ("analogs", "Есть артикул 010500139_? Если нет, предложи аналог и объясни почему."),
                ]:
                    started = time.monotonic()
                    response = await client.post("/api/chat/messages", json={
                        "request_id": str(uuid4()), "text": text, "attachment_ids": [], "language": "ru"})
                    data = response.json()
                    cart = (await client.get("/api/cart")).json()
                    record = {"case": name, "status": response.status_code,
                              "duration_seconds": round(time.monotonic() - started, 3),
                              "result": data, "cart_line_count": cart.get("line_count")}
                    if response.status_code == 200:
                        if name == "terms":
                            record["passed"] = bool(data.get("text")) and cart["line_count"] == 0
                        elif name == "product":
                            product = next((p for p in data.get("products", []) if p["id"] == 21449), {})
                            record["passed"] = (bool(product.get("characteristics"))
                                and any(d["type"] == "certificate" for d in product.get("documents", []))
                                and bool(data.get("proposal")) and cart["line_count"] == 0
                                and data["proposal"]["items"][0]["quantity"] == "2")
                        elif name == "confirm":
                            record["passed"] = (data.get("cart_url") == "/cart" and cart["line_count"] == 1
                                                and cart["items"][0]["quantity"] == "2")
                        else:
                            record["passed"] = ({19457, 21449}.issubset({p["id"] for p in data.get("products", [])})
                                                and cart["items"][0]["quantity"] == "2")
                    else:
                        record["passed"] = False
                    results.append(record)
                    print(json.dumps({k: v for k, v in record.items() if k != "result"}), flush=True)
            finally:
                async with get_session_factory()() as db:
                    session = await resolve_session_from_cookie(db, client.cookies.get(COOKIE_NAME))
                    if session:
                        await db.execute(delete(Session).where(Session.id == session.id))
                        await db.commit()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-db", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    load_local_env()
    results = asyncio.run(verify(args.prepare_db))
    if args.output:
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if all(r["passed"] for r in results) else 1)
