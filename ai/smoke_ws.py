"""Live WebSocket chat check with real OpenAI and EKT reads."""

from uuid import uuid4

from fastapi.testclient import TestClient

from ai.evaluate_hypotheses import load_local_env


def main() -> None:
    load_local_env()
    from backend.main import app
    from backend.sessions import COOKIE_NAME

    with TestClient(app, base_url="http://localhost:5173") as client:
        session = client.post("/api/session", json={}, headers={"Origin": "http://localhost:5173"})
        session.raise_for_status()
        deltas = []
        products = []
        completed = None
        cookie = client.cookies.get(COOKIE_NAME)
        assert cookie
        with client.websocket_connect("/api/chat/ws", headers={
            "Origin": "http://localhost:5173", "Cookie": f"{COOKIE_NAME}={cookie}",
        }) as ws:
            ws.send_json({
                "type": "message.send", "request_id": str(uuid4()),
                "text": "Проверь наличие и сертификат товара 010500006_",
                "attachment_ids": [], "language": "ru",
            })
            while completed is None:
                event = ws.receive_json()
                if event["type"] == "error":
                    raise AssertionError(event["data"])
                if event["type"] == "assistant.delta":
                    deltas.append(event["data"]["text"])
                if event["type"] == "products.ready":
                    products = event["data"]["products"]
                if event["type"] == "assistant.completed":
                    completed = event["data"]
        assert len(deltas) > 1, "WebSocket did not receive streamed text"
        assert completed["text"] == "".join(deltas)
        assert [item["id"] for item in products] == [21449]
        print("PASS", "deltas", len(deltas), "product_id", products[0]["id"])


if __name__ == "__main__":
    main()
