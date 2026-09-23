"""Contract and safety checks without external API calls or a database."""

import json
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from backend.assistant import service
from backend.assistant.files import office_text, attachment_content
from backend.assistant_contract import AssistantContext
from backend.errors import APIError


PRODUCT = {"id": 21449, "name": "Test breaker", "price": "100.00", "available_quantity": "5",
           "quantity_step": "1", "unit": "piece", "characteristics": [], "documents": []}


def final(**overrides):
    result = dict(language="ru", text="Проверенный ответ", product_ids=[21449],
                  proposed_items=[], warnings=[])
    result.update(overrides)
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(result)}]}]}


def call(name="get_product", args=None):
    return {"status": "completed", "output": [{"type": "function_call", "call_id": "call_1",
            "name": name, "arguments": json.dumps(args or {"product_id": 21449})}]}


async def streamed_result(_client, _payload, on_text, answer):
    raw = answer["output"][0]["content"][0]["text"]
    for start in range(0, len(raw), 7):
        await on_text(raw[start:start + 7])
    return answer


class AssistantTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tools = AsyncMock()
        self.tools.get_product.return_value = PRODUCT.copy()
        self.emit = AsyncMock()

    async def test_real_entrypoint_validates_and_emits_matching_text(self):
        answer = final(proposed_items=[{"product_id": 21449, "quantity": "2"}])
        async def stream(client, payload, on_text):
            return await streamed_result(client, payload, on_text, answer)
        with patch.object(service, "_request", AsyncMock(return_value=call())) as model, \
             patch.object(service, "_stream_request", side_effect=stream) as streaming:
            result = await service.reply(AssistantContext(text="Нужно 2 штуки"), self.tools, self.emit)
        self.assertEqual(result.proposed_items[0].quantity, "2")
        self.assertEqual(model.await_count, 1)
        streaming.assert_awaited_once()
        self.tools.get_product.assert_awaited_once_with(21449)
        deltas = [c.args[0]["text"] for c in self.emit.await_args_list if c.args[0]["type"] == "text.delta"]
        self.assertEqual("".join(deltas), result.text)
        self.assertGreater(len(deltas), 1)
        self.assertTrue(all("confirm" not in t["name"] for t in service.FUNCTIONS))

    async def test_unverified_product_never_emitted(self):
        with patch.object(service, "_request", AsyncMock(return_value=final())):
            with self.assertRaises(APIError):
                await service.reply(AssistantContext(text="товар"), self.tools, self.emit)
        self.assertFalse(any(c.args[0]["type"] == "text.delta" for c in self.emit.await_args_list))

    async def test_stock_includes_existing_cart(self):
        answer = final(proposed_items=[{"product_id": 21449, "quantity": "3"}])
        async def stream(client, payload, on_text):
            return await streamed_result(client, payload, on_text, answer)
        with patch.object(service, "_request", AsyncMock(return_value=call())), \
             patch.object(service, "_stream_request", side_effect=stream):
            with self.assertRaises(APIError):
                await service.reply(AssistantContext(text="3 штуки", cart={"items": [
                    {"product_id": 21449, "quantity": "3"}]}), self.tools, self.emit)

    async def test_unknown_tools_and_extra_arguments_rejected(self):
        evidence = service.Evidence(self.tools)
        self.assertIn("error", await evidence.execute("confirm_cart", "{}"))
        self.assertIn("error", await evidence.execute("get_product", '{"product_id":21449,"confirmed":true}'))
        self.tools.get_product.assert_not_awaited()

    async def test_tool_errors_do_not_leak_exception_text(self):
        self.tools.get_product.side_effect = RuntimeError("private-secret")
        evidence = service.Evidence(self.tools)
        result = await evidence.execute("get_product", '{"product_id":21449}')
        self.assertEqual(result["error"], "LOOKUP_FAILED")
        self.assertNotIn("private-secret", json.dumps(result))

    async def test_no_key_fails_before_model_call(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}), patch.object(service, "_request", AsyncMock()) as model:
            with self.assertRaises(APIError):
                await service.reply(AssistantContext(text="test"), self.tools, self.emit)
            model.assert_not_awaited()

    async def test_invalid_model_json_is_safe(self):
        data = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "garbage"}]}]}
        with patch.object(service, "_request", AsyncMock(return_value=data)):
            with self.assertRaises(APIError):
                await service.reply(AssistantContext(text="test"), self.tools, self.emit)

    async def test_streamed_json_decodes_escapes_without_leaking_json(self):
        decoder = service._TextFieldStream()
        raw = json.dumps({"language": "ru", "text": 'Товар "IEK" — 2 шт. 😀',
                          "product_ids": [], "proposed_items": [], "warnings": []})
        chunks = [raw[:31], raw[31:37], raw[37:45], raw[45:53], raw[53:]]
        visible = "".join(decoder.feed(chunk) for chunk in chunks)
        self.assertEqual(visible, 'Товар "IEK" — 2 шт. 😀')
        self.assertNotIn("product_ids", visible)


class AttachmentTest(unittest.TestCase):
    def test_docx_and_xlsx_extract_without_executing_formulas(self):
        data = BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>21449 qty 2</w:t></w:r></w:p></w:document>')
            z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>21449</t></is></c><c r="B1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>')
        self.assertIn("21449 qty 2", office_text(data.getvalue(), ".docx"))
        self.assertIn("cached formula", office_text(data.getvalue(), ".xlsx"))

    def test_outside_upload_directory_rejected(self):
        with self.assertRaises(APIError):
            attachment_content([SimpleNamespace(storage_path="README.md", filename="secret.pdf")])


if __name__ == "__main__":
    unittest.main()
