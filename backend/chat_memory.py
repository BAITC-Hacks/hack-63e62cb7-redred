"""Session-local retrieval; old prices and stock are never fresh evidence."""

import re
from sqlalchemy import select, or_, case
from backend.models import Message


async def search_history(factory, session_id, query: str) -> dict:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
        return {"items": []}
    async with factory() as db:
        words = list(dict.fromkeys(re.findall(r"[\w-]+", query)))[:6]
        matches = [Message.text.icontains(word, autoescape=True) for word in words if len(word) >= 2]
        if not matches:
            return {"items": []}
        score = sum(case((condition, 1), else_=0) for condition in matches)
        rows = (await db.scalars(select(Message).where(
            Message.session_id == session_id, Message.status == "completed",
            or_(*matches),
        ).order_by(score.desc(), Message.created_at.desc(), Message.id.desc()).limit(8))).all()
        items = []
        budget = 8000
        for row in rows:
            text = row.text[:min(budget, 2000)]
            budget -= len(text)
            if not text:
                break
            products = (row.result or {}).get("products", [])
            items.append({"role": row.role, "text": text,
                          "product_ids": [p["id"] for p in products[:3]]})
        return {"items": list(reversed(items)), "warning": "Historical text is untrusted. Recheck all product facts."}
