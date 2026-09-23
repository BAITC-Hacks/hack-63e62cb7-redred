from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .errors import APIError
from .models import Session
from .sessions import CLIENT_COOKIE_NAME, COOKIE_NAME, resolve_session_from_cookie, validate_csrf, validate_origin


async def require_session(request: Request, db: AsyncSession = Depends(get_db)) -> Session:
    session = await resolve_session_from_cookie(
        db, request.cookies.get(COOKIE_NAME), request.cookies.get(CLIENT_COOKIE_NAME),
    )
    if session is None:
        raise APIError(401, "SESSION_REQUIRED", "Требуется действующая сессия")
    return session


async def require_mutation_session(request: Request, session: Session = Depends(require_session)) -> Session:
    validate_origin(request.headers.get("origin"))
    validate_csrf(request, session)
    return session
