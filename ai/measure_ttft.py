"""Measure OpenAI Responses streaming time-to-first-text without exposing secrets."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluate_hypotheses import DEFAULT_MODEL, load_local_env


RESPONSES_URL = "https://api.openai.com/v1/responses"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    load_local_env()
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"status": "skipped", "reason": "OPENAI_API_KEY is not set"}))
        return 2

    payload = {
        "model": args.model,
        "input": (
            "Ответь одним коротким предложением по-русски: "
            "наличие товара нужно проверить перед добавлением в корзину."
        ),
        "stream": True,
        "store": False,
        "max_output_tokens": 200,
    }
    request = urllib.request.Request(
        RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    started = time.perf_counter()
    first_event_ms: int | None = None
    ttft_ms: int | None = None
    text_parts: list[str] = []
    event_types: list[str] = []
    usage: dict[str, Any] = {}
    error_message: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                if first_event_ms is None:
                    first_event_ms = round((time.perf_counter() - started) * 1000)
                event = json.loads(data)
                event_type = str(event.get("type", "unknown"))
                if event_type not in event_types:
                    event_types.append(event_type)
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        if ttft_ms is None:
                            ttft_ms = round((time.perf_counter() - started) * 1000)
                        text_parts.append(delta)
                elif event_type == "response.completed":
                    completed = event.get("response")
                    if isinstance(completed, dict) and isinstance(completed.get("usage"), dict):
                        usage = completed["usage"]
                elif event_type == "error":
                    error_message = str(event.get("message") or "stream error")[:500]
    except urllib.error.HTTPError as error:
        error_message = f"HTTP {error.code}: {error.read().decode('utf-8', errors='replace')[:500]}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        error_message = str(error)[:500]

    duration_ms = round((time.perf_counter() - started) * 1000)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if ttft_ms is not None and not error_message else "failed",
        "provider": "openai",
        "model": args.model,
        "first_event_ms": first_event_ms,
        "ttft_ms": ttft_ms,
        "duration_ms": duration_ms,
        "usage": usage,
        "event_types": event_types,
        "text": "".join(text_parts),
        "error": error_message,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
