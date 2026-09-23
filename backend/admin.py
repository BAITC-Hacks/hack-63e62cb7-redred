"""Separate operator login and protected catalog synchronization controls."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
import hashlib
import hmac
from pathlib import Path
import secrets
import time
from uuid import UUID

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .errors import APIError
from .models import AdminSession, CartItem, CartProposal, Message, ProductSnapshot, Session, utcnow


router = APIRouter(tags=["admin"])
ADMIN_COOKIE = "hackalem_admin"
ADMIN_TTL = timedelta(hours=8)
_attempts: dict[str, deque[float]] = defaultdict(deque)
_PAGE = Path(__file__).parent / "static" / "admin.html"


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class TriggerIn(BaseModel):
    mode: str


def _configured() -> None:
    if not get_settings().admin_password:
        raise APIError(503, "ADMIN_NOT_CONFIGURED", "Пароль администратора не настроен")


def _origin(request: Request) -> None:
    if request.headers.get("origin") != get_settings().admin_origin:
        raise APIError(403, "ORIGIN_FORBIDDEN", "Недопустимый Origin")


def _rate_limit(request: Request) -> None:
    now = time.monotonic()
    ip = request.client.host if request.client else "unknown"
    # Keep the one-process limiter bounded even if clients rotate addresses.
    if len(_attempts) > 4096:
        for key in list(_attempts):
            queue = _attempts[key]
            if not queue or queue[-1] <= now - 60:
                _attempts.pop(key, None)
    if ip not in _attempts and len(_attempts) >= 4096:
        raise APIError(429, "ADMIN_RATE_LIMITED", "Слишком много попыток. Подождите минуту")
    queue = _attempts[ip]
    while queue and queue[0] <= now - 60:
        queue.popleft()
    if len(queue) >= 5:
        raise APIError(429, "ADMIN_RATE_LIMITED", "Слишком много попыток. Подождите минуту")
    queue.append(now)


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AdminSession:
    _configured()
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        raise APIError(401, "ADMIN_AUTH_REQUIRED", "Нужен вход администратора")
    hashed = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = await db.scalar(select(AdminSession).where(AdminSession.token_hash == hashed))
    if row is None or row.expires_at <= utcnow():
        raise APIError(401, "ADMIN_AUTH_REQUIRED", "Нужен вход администратора")
    return row


def _mutation(request: Request, session: AdminSession) -> None:
    _origin(request)
    supplied = request.headers.get("X-CSRF-Token", "")
    if not hmac.compare_digest(supplied, session.csrf_token):
        raise APIError(403, "CSRF_INVALID", "Недействительный CSRF-токен")


def _service(request: Request):
    service = getattr(request.app.state, "catalog_sync", None)
    if service is None:
        raise APIError(503, "SYNC_UNAVAILABLE", "Синхронизация каталога недоступна")
    return service


@router.get("/admin", include_in_schema=False)
@router.get("/admin/catalog", include_in_schema=False)
async def catalog_admin_page() -> FileResponse:
    return FileResponse(_PAGE, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.post("/api/admin/session")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    _configured()
    _origin(request)
    _rate_limit(request)
    if not hmac.compare_digest(body.password.encode("utf-8"), get_settings().admin_password.encode("utf-8")):
        raise APIError(401, "ADMIN_INVALID_CREDENTIALS", "Неверный пароль")
    token = secrets.token_urlsafe(48)
    row = AdminSession(
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        csrf_token=secrets.token_urlsafe(32), expires_at=utcnow() + ADMIN_TTL,
    )
    db.add(row)
    await db.commit()
    response.set_cookie(
        ADMIN_COOKIE, token, httponly=True, secure=get_settings().secure_cookie,
        samesite="lax", max_age=int(ADMIN_TTL.total_seconds()), path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "role": "admin", "csrf_token": row.csrf_token, "expires_at": row.expires_at.isoformat()}


@router.get("/api/admin/session")
async def current_admin(response: Response, session: AdminSession = Depends(require_admin)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "role": "admin", "csrf_token": session.csrf_token, "expires_at": session.expires_at.isoformat()}


@router.delete("/api/admin/session")
async def logout(
    request: Request, response: Response,
    session: AdminSession = Depends(require_admin), db: AsyncSession = Depends(get_db),
) -> dict:
    _mutation(request, session)
    await db.execute(delete(AdminSession).where(AdminSession.id == session.id))
    await db.commit()
    response.delete_cookie(ADMIN_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}


@router.get("/api/admin/catalog/sync")
async def sync_status(request: Request, session: AdminSession = Depends(require_admin)) -> dict:
    return await _service(request).status()


@router.post("/api/admin/catalog/sync", status_code=202)
async def trigger_sync(
    body: TriggerIn, request: Request, session: AdminSession = Depends(require_admin),
) -> dict:
    _mutation(request, session)
    if body.mode not in {"full", "existing"}:
        raise APIError(422, "INVALID_SYNC_MODE", "Допустимы режимы full и existing")
    return await _service(request).trigger(mode=body.mode, trigger="manual")


@router.post("/api/admin/catalog/sync/{run_id}/cancel", status_code=202)
async def cancel_sync(
    run_id: UUID, request: Request, session: AdminSession = Depends(require_admin),
) -> dict:
    _mutation(request, session)
    return await _service(request).cancel(run_id)


@router.get("/api/admin/dashboard")
async def dashboard(
    session: AdminSession = Depends(require_admin), db: AsyncSession = Depends(get_db),
) -> dict:
    from .models import User

    counts = {
        "registered_clients": await db.scalar(select(func.count()).select_from(User).where(User.role == "client")),
        "active_guests": await db.scalar(select(func.count()).select_from(Session).outerjoin(
            User, User.session_id == Session.id,
        ).where(User.id.is_(None), Session.expires_at > utcnow())),
        "conversations": await db.scalar(select(func.count(func.distinct(Message.session_id)))),
        "messages": await db.scalar(select(func.count()).select_from(Message)),
        "catalog_products": await db.scalar(select(func.count()).select_from(ProductSnapshot)),
        "cart_items": await db.scalar(select(func.count()).select_from(CartItem)),
        "confirmed_proposals": await db.scalar(select(func.count()).select_from(CartProposal).where(CartProposal.status == "confirmed")),
    }
    return {"counts": {key: int(value or 0) for key, value in counts.items()}}


def _public_chat(row: Session, user: Any, message_count: int | None = None, last_message_at: Any = None) -> dict:
    return {
        "session_id": str(row.id),
        "kind": "client" if user is not None else "guest",
        "user": ({"id": str(user.id), "email": user.email, "name": user.name, "role": user.role} if user is not None else None),
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "message_count": int(message_count or 0),
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
    }


@router.get("/api/admin/chats")
async def list_chats(
    session: AdminSession = Depends(require_admin), db: AsyncSession = Depends(get_db),
    kind: Literal["all", "guest", "client"] = "all",
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0),
) -> dict:
    from .models import User

    stats = select(
        Message.session_id.label("session_id"),
        func.count(Message.id).label("message_count"),
        func.max(Message.created_at).label("last_message_at"),
    ).group_by(Message.session_id).subquery()
    statement = select(Session, User, stats.c.message_count, stats.c.last_message_at).join(
        stats, stats.c.session_id == Session.id,
    ).outerjoin(User, User.session_id == Session.id)
    if kind == "guest":
        statement = statement.where(User.id.is_(None))
    elif kind == "client":
        statement = statement.where(User.id.is_not(None))
    if q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.where(or_(
            User.email.ilike(pattern), User.name.ilike(pattern), cast(Session.id, String).ilike(pattern),
        ))
    total = await db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = (await db.execute(statement.order_by(
        stats.c.last_message_at.desc(), Session.id.desc(),
    ).limit(limit).offset(offset))).all()
    return {
        "items": [_public_chat(row, user, count, last) for row, user, count, last in rows],
        "total": int(total or 0), "limit": limit, "offset": offset,
    }


@router.get("/api/admin/chats/{session_id}/messages")
async def chat_messages(
    session_id: UUID, session: AdminSession = Depends(require_admin), db: AsyncSession = Depends(get_db),
    limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
) -> dict:
    from .models import User

    row = await db.get(Session, session_id)
    if row is None:
        raise APIError(404, "CHAT_NOT_FOUND", "Чат не найден")
    user = await db.scalar(select(User).where(User.session_id == session_id))
    total = await db.scalar(select(func.count()).select_from(Message).where(Message.session_id == session_id))
    messages = (await db.scalars(select(Message).where(Message.session_id == session_id).order_by(
        Message.created_at.desc(), Message.id.desc(),
    ).limit(limit).offset(offset))).all()
    return {
        "session": _public_chat(row, user, total, messages[0].created_at if messages and offset == 0 else None),
        "items": [{
            "id": str(item.id), "role": item.role, "text": item.text,
            "status": item.status, "language": item.language,
            "attachment_count": len(item.attachment_ids or []),
            "created_at": item.created_at.isoformat(),
        } for item in messages],
        "total": int(total or 0), "limit": limit, "offset": offset,
    }


@router.get("/api/admin/settings")
async def admin_settings(
    session: AdminSession = Depends(require_admin), db: AsyncSession = Depends(get_db),
) -> dict:
    from .runtime_settings import read_settings

    return await read_settings(db)


@router.patch("/api/admin/settings")
async def patch_admin_settings(
    patch: dict[str, Any], request: Request,
    session: AdminSession = Depends(require_admin), db: AsyncSession = Depends(get_db),
) -> dict:
    from .runtime_settings import update_settings

    _mutation(request, session)
    result = await update_settings(db, patch)
    await _service(request).reload()
    return result
