"""One account transition across real HTTP/WS routes; no model or EKT calls."""

import os
import secrets
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.config import get_settings
from backend.db import get_session_factory
from backend.models import AdminSession, Session, User
from backend.sessions import COOKIE_NAME, token_hash


class AccountFlowTest(unittest.TestCase):
    def test_guest_register_logout_login_and_admin_history(self):
        email = f"flow-{uuid4().hex}@example.test"
        password = secrets.token_urlsafe(24)
        admin_password = secrets.token_urlsafe(24)
        session_ids = set()
        admin_hashes = set()
        environment = {
            "APP_ORIGIN": "http://localhost:5173",
            "ADMIN_ORIGIN": "http://localhost:8000",
            "ADMIN_PASSWORD": admin_password,
            "CATALOG_SYNC_ENABLED": "false",
            "EKT_API_USERNAME": "",
            "EKT_API_PASSWORD": "",
        }

        async def remember_guest(token):
            async with get_session_factory()() as db:
                identifier = await db.scalar(select(Session.id).where(Session.token_hash == token_hash(token)))
                if identifier:
                    session_ids.add(identifier)

        async def cleanup():
            async with get_session_factory()() as db:
                user = await db.scalar(select(User).where(User.email == email))
                if user:
                    session_ids.add(user.session_id)
                    await db.delete(user)
                    await db.flush()
                await db.execute(delete(Session).where(Session.id.in_(session_ids)))
                await db.execute(delete(AdminSession).where(AdminSession.token_hash.in_(admin_hashes)))
                await db.commit()

        with patch.dict(os.environ, environment):
            get_settings.cache_clear()
            from backend.main import app

            with TestClient(app, base_url="http://localhost:8000", headers={"Origin": environment["APP_ORIGIN"]}) as client:
                try:
                    guest = client.post("/api/session")
                    self.assertEqual(guest.status_code, 200, guest.text)
                    client.portal.call(remember_guest, client.cookies.get(COOKIE_NAME))
                    guest_headers = {"X-CSRF-Token": guest.json()["csrf_token"]}
                    favorite = client.put("/api/favorites/21449", headers=guest_headers)
                    self.assertEqual(favorite.status_code, 200, favorite.text)
                    message = client.post("/api/chat/messages", headers=guest_headers, json={
                        "request_id": str(uuid4()), "text": "да добавь",
                    })
                    self.assertEqual(message.status_code, 200, message.text)
                    history = client.get("/api/chat/messages").json()["items"]
                    self.assertEqual(len(history), 2)

                    with client.websocket_connect("ws://localhost:8000/api/chat/ws", headers={"Origin": environment["APP_ORIGIN"]}) as old_guest_socket:
                        registered = client.post("/api/auth/register", headers=guest_headers, json={
                            "email": email, "password": password, "name": "Проверка перехода",
                        })
                        self.assertEqual(registered.status_code, 200, registered.text)
                        self.assertEqual(registered.json()["user"]["role"], "client")
                        old_guest_socket.send_json({"type": "message.send", "request_id": str(uuid4()), "text": "да добавь"})
                        self.assertEqual(old_guest_socket.receive()["code"], 4401)

                    self.assertEqual(client.get("/api/favorites").json()["total"], 1)
                    self.assertEqual(client.get("/api/chat/messages").json()["items"], history)
                    self.assertEqual(client.get("/api/admin/dashboard").status_code, 401)
                    account_headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
                    with client.websocket_connect("ws://localhost:8000/api/chat/ws", headers={"Origin": environment["APP_ORIGIN"]}) as old_client_socket:
                        logout = client.post("/api/auth/logout", headers=account_headers)
                        self.assertEqual(logout.status_code, 200, logout.text)
                        old_client_socket.send_json({"type": "message.send", "request_id": str(uuid4()), "text": "да добавь"})
                        self.assertEqual(old_client_socket.receive()["code"], 4401)

                    next_guest = client.post("/api/session")
                    self.assertEqual(next_guest.status_code, 200, next_guest.text)
                    client.portal.call(remember_guest, client.cookies.get(COOKIE_NAME))
                    self.assertIsNone(next_guest.json()["user"])
                    self.assertEqual(client.get("/api/favorites").json()["total"], 0)
                    self.assertEqual(client.get("/api/chat/messages").json()["items"], [])
                    login = client.post("/api/auth/login", headers={"X-CSRF-Token": next_guest.json()["csrf_token"]}, json={
                        "email": email, "password": password,
                    })
                    self.assertEqual(login.status_code, 200, login.text)
                    self.assertEqual(client.get("/api/favorites").json()["total"], 1)
                    self.assertEqual(client.get("/api/chat/messages").json()["items"], history)

                    operator = client.post("/api/admin/session", headers={"Origin": environment["ADMIN_ORIGIN"]}, json={"password": admin_password})
                    self.assertEqual(operator.status_code, 200, operator.text)
                    admin_hashes.add(token_hash(client.cookies.get("hackalem_admin")))
                    self.assertEqual(operator.json()["role"], "admin")
                    chats = client.get("/api/admin/chats", params={"q": email, "kind": "client"})
                    self.assertEqual(chats.status_code, 200, chats.text)
                    self.assertEqual(chats.json()["total"], 1)
                    identifier = chats.json()["items"][0]["session_id"]
                    transcript = client.get(f"/api/admin/chats/{identifier}/messages")
                    self.assertEqual(transcript.status_code, 200, transcript.text)
                    self.assertEqual(transcript.json()["total"], 2)
                    self.assertEqual(client.get("/api/admin/dashboard").status_code, 200)
                finally:
                    client.portal.call(cleanup)
            get_settings.cache_clear()
