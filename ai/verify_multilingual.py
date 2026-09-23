"""Live multilingual HTTP checks. Requires migrated test DB, demo catalog and API keys."""

import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4
from unittest.mock import patch

import httpx
from ai.evaluate_hypotheses import load_local_env


async def verify():
    from backend.main import app
    from backend.db import get_session_factory
    from backend.models import Session, Message
    from backend.sessions import COOKIE_NAME, resolve_session_from_cookie
    from backend.assistant.service import Evidence
    from sqlalchemy import delete

    records = []
    cases = [
        ("kazakh", "kk", "Маған 010500006_ артикулынан 2 дана керек. Сипаттамасы, сертификаты және қалдығы қандай? Ұсыныс дайында."),
        ("english_analog", "en", "Is article 010500139_ available? If not, suggest an alternative and explain the differences."),
        ("mixed_quantity", "kk", "Маған 010500006_ артикулдан 999999 штуки керек, stock жетеді ме? Если нет предложи alternative."),
        ("long_session", "kk", "Бұрын талқылаған IEK автоматы туралы тарихтан тап. Сол тауардан 2 дана керек, ұсыныс дайында."),
    ]
    async with app.router.lifespan_context(app):
        for name, language, text in cases:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                         headers={"Origin": "http://localhost:5173"}) as client:
                response = await client.post("/api/session", json={})
                response.raise_for_status()
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
                async with get_session_factory()() as db:
                    owner = await resolve_session_from_cookie(db, client.cookies.get(COOKIE_NAME))
                    owner_id = owner.id
                    if name == "long_session":
                        db.add(Message(session_id=owner_id, request_id=uuid4(), role="assistant",
                            text="IEK: article 010500006_, internal product_id 21449.", attachment_ids=[],
                            status="completed", result={"products": [{"id": 21449}]}))
                        await db.commit()
                        for n in range(16):
                            db.add(Message(session_id=owner_id, request_id=uuid4(), role="user", text=f"Other topic {n}",
                                           attachment_ids=[], status="completed"))
                        await db.commit()
                try:
                    traces = []
                    execute = Evidence.execute
                    async def traced(evidence, tool, arguments):
                        value = await execute(evidence, tool, arguments)
                        traces.append({"tool": tool, "arguments": json.loads(arguments), "error": value.get("error"), "item_count": len(value.get("items", []))})
                        return value
                    start = time.monotonic()
                    with patch.object(Evidence, "execute", traced):
                        response = await client.post("/api/chat/messages", json={"request_id": str(uuid4()), "text": text, "language": language})
                    data = response.json()
                    cart = (await client.get("/api/cart")).json()
                    passed = response.status_code == 200 and data.get("language") == language and not cart["items"]
                    if name in {"kazakh", "long_session"}:
                        passed = passed and bool(data.get("proposal")) and data["proposal"]["items"][0]["quantity"] == "2"
                    if name == "english_analog":
                        passed = passed and {19457, 21449}.issubset({p["id"] for p in data.get("products", [])})
                    if name == "mixed_quantity":
                        passed = passed and not data.get("proposal") and bool(data.get("text"))
                    record = {"case": name, "status": response.status_code, "language": data.get("language"),
                              "duration_seconds": round(time.monotonic() - start, 3), "passed": bool(passed),
                              "text": data.get("text"), "error": data.get("error"), "tools": traces}
                    records.append(record)
                    print(json.dumps({k: v for k, v in record.items() if k != "text"}), flush=True)
                finally:
                    async with get_session_factory()() as db:
                        await db.execute(delete(Session).where(Session.id == owner_id))
                        await db.commit()
    Path("ai/reports/backend-multilingual-live.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return all(r["passed"] for r in records)


if __name__ == "__main__":
    load_local_env()
    raise SystemExit(0 if asyncio.run(verify()) else 1)
