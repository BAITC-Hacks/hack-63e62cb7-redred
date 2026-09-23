import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import get_db
from .errors import APIError
from .models import Cart, ClientLogin, Session, User, utcnow


router = APIRouter(prefix="/api", tags=["session"])
COOKIE_NAME = "hackalem_session"
CLIENT_COOKIE_NAME = "hackalem_client"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_origin(origin: str | None, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if origin != settings.app_origin:
        raise APIError(403, "ORIGIN_FORBIDDEN", "Недопустимый Origin")


def validate_csrf(request: Request, session: Session) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = getattr(session, "_request_csrf_token", session.csrf_token)
    if not secrets.compare_digest(supplied, expected):
        raise APIError(403, "CSRF_INVALID", "Недействительный CSRF-токен")


async def resolve_session_from_cookie(
    db: AsyncSession, token: str | None, client_token: str | None = None,
) -> Session | None:
    if client_token:
        login = await db.scalar(select(ClientLogin).where(ClientLogin.token_hash == token_hash(client_token)))
        if login is not None and login.expires_at > utcnow():
            session = await db.get(Session, login.session_id)
            user = await db.get(User, login.user_id)
            if session is not None and user is not None and user.session_id == session.id:
                session._request_csrf_token = login.csrf_token
                session._request_expires_at = login.expires_at
                session._client_login_id = login.id
                session._auth_user = user
                return session
    if not token:
        return None
    session = await db.scalar(select(Session).where(Session.token_hash == token_hash(token)))
    if session is None or session.expires_at <= utcnow():
        return None
    # A former guest cookie must not regain a registered account's session.
    if await db.scalar(select(User.id).where(User.session_id == session.id)) is not None:
        return None
    return session


@router.post("/session")
async def create_session(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    validate_origin(request.headers.get("origin"))
    session = await resolve_session_from_cookie(
        db, request.cookies.get(COOKIE_NAME), request.cookies.get(CLIENT_COOKIE_NAME),
    )
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
    user = getattr(session, "_auth_user", None)
    return {
        "csrf_token": getattr(session, "_request_csrf_token", session.csrf_token),
        "expires_at": getattr(session, "_request_expires_at", session.expires_at).isoformat(),
        "user": {"id": str(user.id), "email": user.email, "name": user.name, "role": user.role} if user else None,
    }
