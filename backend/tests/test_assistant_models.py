"""Focused fake-provider checks for model fallback and capability probing."""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from backend.ai_control import AIModelControl, ModelSelection
from backend.ai_provider import AIProviderFailure, from_http_response
from backend.assistant import service
from backend.assistant_contract import AssistantContext
from backend.errors import APIError


def result(text="OK", language="ru"):
    return {"status": "completed", "output": [{"type": "message", "content": [{
        "type": "output_text", "text": json.dumps({
            "language": language, "text": text,
            "product_ids": [], "proposed_items": [], "warnings": [],
        }, ensure_ascii=False),
    }]}]}


class AssistantModelsTest(unittest.IsolatedAsyncioTestCase):
    def _control(self):
        control = AIModelControl(None, None)
        control.selection = AsyncMock(return_value=ModelSelection("bad-model", "gpt-6-sol", 7, True))
        control.record_failure = AsyncMock(return_value=True)
        control.record_success = AsyncMock()
        return control

    async def test_provider_classification_distinguishes_quota_from_rate_and_model(self):
        def failure(status, code):
            return from_http_response(httpx.Response(status, json={"error": {"code": code, "message": "secret"}}))
        self.assertFalse(failure(429, "insufficient_quota").fallback_allowed)
        self.assertTrue(failure(429, "rate_limit_exceeded").fallback_allowed)
        self.assertTrue(failure(404, "model_not_found").fallback_allowed)
        self.assertFalse(failure(401, "invalid_api_key").fallback_allowed)
        self.assertTrue(failure(503, "server_is_overloaded").fallback_allowed)
        self.assertNotIn("secret", failure(429, "insufficient_quota").admin_message)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"status": "failed", "error": {"code": "server_is_overloaded", "message": "secret"}})
        )) as client:
            with self.assertRaises(AIProviderFailure) as caught:
                await service._request(client, {"model": "gpt-6-sol"})
        self.assertEqual(caught.exception.code, "server_is_overloaded")
        self.assertTrue(caught.exception.fallback_allowed)

    async def test_override_failure_retries_default_once_before_text(self):
        control = self._control()
        tools = SimpleNamespace(model_control=control)
        calls = []

        async def fake_request(_client, payload):
            calls.append(payload["model"])
            if len(calls) == 1:
                raise AIProviderFailure("model_not_found", "OpenAI HTTP 404", True, 404)
            return result()

        events = []
        async def emit(event):
            events.append(event)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}), patch.object(service, "_request", fake_request):
            answer = await service.reply(AssistantContext(text="Привет", language="ru"), tools, emit)
        self.assertEqual(answer.text, "OK")
        self.assertEqual(calls, ["bad-model", "gpt-6-sol"])
        self.assertEqual([event["text"] for event in events if event["type"] == "text.delta"], ["OK"])
        control.record_failure.assert_awaited_once()
        self.assertEqual(control.record_success.await_args.args[0], ModelSelection("gpt-6-sol", "gpt-6-sol", 8, False))

    async def test_partial_stream_never_retries_and_invalid_result_is_not_provider_failure(self):
        control = self._control()
        tools = SimpleNamespace(model_control=control, get_cart=AsyncMock(return_value={"items": []}))
        first = {"status": "completed", "output": [{
            "type": "function_call", "name": "get_cart", "arguments": "{}", "call_id": "call_1",
        }]}
        request = AsyncMock(return_value=first)

        async def broken_stream(_client, _payload, on_text):
            await on_text('{"language":"ru","text":"Часть"')
            raise AIProviderFailure("server_error", "OpenAI HTTP 503", True, 503)

        events = []
        async def emit(event):
            events.append(event)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}), patch.object(service, "_request", request), patch.object(service, "_stream_request", broken_stream):
            with self.assertRaises(APIError) as caught:
                await service.reply(AssistantContext(text="Корзина", language="ru"), tools, emit)
        self.assertEqual(caught.exception.code, "ASSISTANT_UNAVAILABLE")
        self.assertEqual(request.await_count, 1)
        self.assertEqual([event["text"] for event in events if event["type"] == "text.delta"], ["Часть"])
        control.record_failure.assert_awaited_once()

        control.record_failure.reset_mock()
        invalid = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "bad json"}]}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}), patch.object(service, "_request", AsyncMock(return_value=invalid)):
            with self.assertRaises(APIError) as invalid_error:
                await service.reply(AssistantContext(text="Привет", language="ru"), tools, emit)
        self.assertEqual(invalid_error.exception.code, "ASSISTANT_INVALID_RESULT")
        control.record_failure.assert_not_awaited()

    async def test_probe_requires_forced_tool_and_structured_stream(self):
        final = result("Capability check passed", "en")
        final_text = final["output"][0]["content"][0]["text"]
        requests = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            requests.append(body)
            self.assertEqual(request.headers["authorization"], "Bearer fake")
            self.assertEqual(body["model"], "gpt-6-sol")
            self.assertTrue(all(tool["strict"] for tool in body["tools"]))
            if len(requests) == 1:
                return httpx.Response(200, json={"status": "completed", "output": [{
                    "type": "function_call", "name": "get_cart", "arguments": "{}", "call_id": "call_1",
                }]})
            events = [
                {"type": "response.output_text.delta", "delta": final_text},
                {"type": "response.completed", "response": final},
            ]
            content = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                await service.probe_model(client, "gpt-6-sol")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["tool_choice"], {"type": "function", "name": "get_cart"})
        self.assertEqual(requests[1]["tool_choice"], "none")
        self.assertTrue(requests[1]["stream"])


if __name__ == "__main__":
    unittest.main()
