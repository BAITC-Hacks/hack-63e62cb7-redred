import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from backend.assistant import service
from backend.assistant_contract import AssistantContext
from tests.test_assistant_service import PRODUCT, final, streamed_result

class FastArticleTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_reply_can_finish_after_previous_eight_second_limit(self):
        tools = AsyncMock()
        tools.search_catalog.return_value = {"items": [{"id": 21449}], "total": 1}
        tools.get_product.return_value = PRODUCT.copy()
        emit = AsyncMock()

        async def slow_stream(client, payload, on_text):
            await asyncio.sleep(8.1)
            return await streamed_result(client, payload, on_text, final())

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}), patch.object(service, "_stream_request", side_effect=slow_stream):
            result = await service.reply(
                AssistantContext(text="Расскажи о товаре 010-005: характеристики, наличие и сертификаты"), tools, emit,
            )
        self.assertEqual(result.product_ids, [21449])
        self.assertEqual("".join(call.args[0]["text"] for call in emit.await_args_list if call.args[0]["type"] == "text.delta"), result.text)

    async def test_missing_product_is_not_reported_as_zero_stock(self):
        tools = AsyncMock()
        tools.search_catalog.return_value = {"items": [], "total": 0, "catalog_scope": "demo_subset"}
        result = await service.Evidence(tools).execute("search_catalog", '{"query":"свечи зажигания","article":null}')
        self.assertEqual(result["match_status"], "not_found")
        self.assertEqual(result["items"], [])
        self.assertIn("not zero stock", result["guidance"])
        tools.find_analogs.assert_not_awaited()

    async def test_verified_article_needs_only_one_streamed_call(self):
        tools = AsyncMock()
        tools.search_catalog.return_value = {"items": [{"id": 21449}], "total": 1}
        tools.get_product.return_value = PRODUCT.copy()
        emit = AsyncMock()
        async def stream(client, payload, on_text):
            self.assertEqual(payload["tool_choice"], "none")
            return await streamed_result(client, payload, on_text, final())
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}), patch.object(service, "_request", AsyncMock()) as initial, patch.object(service, "_stream_request", side_effect=stream) as streaming:
            result = await service.reply(AssistantContext(text="Расскажи о товаре 010-005: характеристики, наличие и сертификаты"), tools, emit)
        initial.assert_not_awaited()
        streaming.assert_awaited_once()
        self.assertEqual(result.product_ids, [21449])
        self.assertEqual("".join(c.args[0]["text"] for c in emit.await_args_list if c.args[0]["type"] == "text.delta"), result.text)
        self.assertIsNone(service._information_article(AssistantContext(text="Добавь 2 штуки 010-005")))
