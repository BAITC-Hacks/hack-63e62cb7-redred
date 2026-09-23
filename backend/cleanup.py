"""Remove expired private uploads and guest sessions.

Run from repository root: python -m backend.cleanup
"""

import asyncio

from sqlalchemy import delete, exists, select

from .attachments import cleanup_expired
from .db import get_session_factory
from .models import ClientLogin, Session, User, utcnow


async def cleanup() -> tuple[int, int]:
    async with get_session_factory()() as db:
        attachments = await cleanup_expired(db)
        await db.execute(delete(ClientLogin).where(ClientLogin.expires_at <= utcnow()))
        result = await db.execute(delete(Session).where(
            Session.expires_at <= utcnow(),
            ~exists(select(User.id).where(User.session_id == Session.id)),
        ))
        await db.commit()
    return attachments, result.rowcount


if __name__ == "__main__":
    deleted_attachments, deleted_sessions = asyncio.run(cleanup())
    print(f"Удалено вложений: {deleted_attachments}; сессий: {deleted_sessions}")
