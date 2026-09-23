"""Live check: an invalid primary key falls back to the configured working key."""

import asyncio
import os

from ai.evaluate_hypotheses import load_local_env
from ai.smoke_service import Tools
from backend.assistant.service import reply
from backend.assistant_contract import AssistantContext


async def main() -> None:
    load_local_env()
    working_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not working_key:
        raise SystemExit("Set OPENAI_API_KEY to a working server-side key for this smoke check")
    os.environ["OPENAI_API_KEY"] = "invalid-key-for-fallback-check"
    os.environ["OPENAI_FALLBACK_API_KEY"] = working_key
    events = []

    async def emit(event: dict) -> None:
        events.append(event)

    result = await reply(
        AssistantContext(text="Проверь товар 010500006_", language="ru"), Tools(), emit,
    )
    deltas = [event["text"] for event in events if event["type"] == "text.delta"]
    assert result.text == "".join(deltas)
    assert 21449 in result.product_ids
    assert len(deltas) > 1
    print("Fallback key: product 21449 verified, text deltas", len(deltas))


if __name__ == "__main__":
    asyncio.run(main())
