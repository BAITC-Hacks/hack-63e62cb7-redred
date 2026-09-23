"""Provision a client from CLIENT_LOGIN and CLIENT_PASSWORD environment variables."""

import asyncio
from datetime import timedelta
import os
import secrets

from sqlalchemy import select

from .auth import Credentials, _hash_password
from .config import get_settings
from .db import get_engine, get_session_factory
from .models import Cart, Session, User, utcnow
from .sessions import token_hash


async def create_client() -> None:
    credentials = Credentials(email=os.environ["CLIENT_LOGIN"].strip().casefold(), password=os.environ["CLIENT_PASSWORD"])
    if not credentials.email or any(char.isspace() for char in credentials.email):
        raise ValueError("CLIENT_LOGIN cannot contain whitespace")
    try:
        async with get_session_factory()() as db, db.begin():
            user = await db.scalar(select(User).where(User.email == credentials.email))
            if user is not None:
                print("Client already exists; password unchanged")
                return
            session = Session(
                token_hash=token_hash(secrets.token_urlsafe(48)),
                csrf_token=secrets.token_urlsafe(32),
                expires_at=utcnow() + timedelta(hours=get_settings().session_hours),
                state={},
            )
            db.add(session)
            await db.flush()
            db.add(Cart(session_id=session.id, revision=0))
            db.add(User(email=credentials.email, name=credentials.email,
                        password_hash=await asyncio.to_thread(_hash_password, credentials.password),
                        role="client", session_id=session.id))
        print("Client created")
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(create_client())
