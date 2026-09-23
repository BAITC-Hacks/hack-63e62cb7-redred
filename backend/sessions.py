import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import get_db
from .errors import APIError
from .models import Cart, Session, utcnow


router = APIRouter(prefix="/api", tags=["session"])
COOKIE_NAME = "hackalem_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_origin(origin: str | None, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if origin != settings.app_origin:
        raise APIError(403, "ORIGIN_FORBIDDEN", "Недопустимый Origin")


def validate_csrf(request: Request, session: Session) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not secrets.compare_digest(supplied, session.csrf_token):
        raise APIError(403, "CSRF_INVALID", "Недействительный CSRF-токен")


async def resolve_session_from_cookie(db: AsyncSession, token: str | None) -> Session | None:
    if not token:
        return None
    session = await db.scalar(select(Session).where(Session.token_hash == token_hash(token)))
    if session is None or session.expires_at <= utcnow():
        return None
    return session


@router.post("/session")
async def create_session(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    validate_origin(request.headers.get("origin"))
    session = await resolve_session_from_cookie(db, request.cookies.get(COOKIE_NAME))
    if session is None:
        token = secrets.token_urlsafe(48)
        session = Session(
            token_hash=token_hash(token),
            csrf_token=secrets.token_urlsafe(32),
            state={},
            expires_at=utcnow() + timedelta(hours=get_settings().session_hours),
        )
        db.add(session)
        await db.flush()
        db.add(Cart(session_id=session.id, revision=0))
        await db.commit()
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=get_settings().secure_cookie,
            samesite="lax",
            max_age=get_settings().session_hours * 3600,
            path="/",
        )
    return {"csrf_token": session.csrf_token, "expires_at": session.expires_at.isoformat()}
