"""Tests for the Gemini adapter through the dispatcher: wire format, signatures, retries."""

import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from zill import provider
from zill.providers import gemini


def http_response(data):
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(data).encode("utf-8")
    return response


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"server says no"))


REPLY = {"candidates": [{"content": {"parts": [
    {"text": "thinking...", "thought": True},
    {"text": "Reading it."},
    {"functionCall": {"name": "read_file", "args": {"path": "a"}}, "thoughtSignature": "sig-1"},
]}}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3}}


@mock.patch.dict(os.environ, {"ZILL_API_KEY": "test-key"})
@mock.patch("time.sleep")
class ProviderTests(unittest.TestCase):
    def test_reply_is_neutral_and_skips_thoughts(self, sleep):
        with mock.patch("urllib.request.urlopen", return_value=http_response(REPLY)):
            reply = provider.complete("m", "sys", [{"role": "user", "text": "hi"}], [])
        self.assertEqual(reply["text"], "Reading it.")
        self.assertEqual(reply["tool_calls"][0]["signature"], "sig-1")
        self.assertEqual(reply["usage"], {"input": 7, "output": 3})

    def test_thought_signature_is_sent_back(self, sleep):
        messages = [{"role": "assistant", "text": "", "tool_calls": [
            {"id": "c1", "name": "read_file", "args": {"path": "a"}, "signature": "sig-1"}]},
            {"role": "tool", "id": "c1", "name": "read_file", "text": "contents"}]
        wire = gemini._to_wire(messages)
        self.assertEqual(wire[0]["parts"][0]["thoughtSignature"], "sig-1")
        self.assertEqual(wire[1]["parts"][0]["functionResponse"]["name"], "read_file")
        self.assertNotIn("id", wire[0]["parts"][0]["functionCall"])  # ZILL-made id: not sent

    def test_gemini_issued_ids_are_echoed(self, sleep):
        messages = [{"role": "assistant", "text": "", "tool_calls": [
            {"id": "call_4650862", "native_id": "call_4650862", "name": "grep", "args": {}}]},
            {"role": "tool", "id": "call_4650862", "name": "grep", "text": "hits"}]
        wire = gemini._to_wire(messages)
        self.assertEqual(wire[0]["parts"][0]["functionCall"]["id"], "call_4650862")
        self.assertEqual(wire[1]["parts"][0]["functionResponse"]["id"], "call_4650862")

    def test_daily_quota_is_not_retried(self, sleep):
        body = b'{"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}}'
        daily = urllib.error.HTTPError("https://x", 429, "quota", {}, io.BytesIO(body))
        with mock.patch("urllib.request.urlopen", side_effect=[daily]):
            with self.assertRaisesRegex(RuntimeError, "daily quota"):
                provider.complete("m", "sys", [{"role": "user", "text": "hi"}], [])
        sleep.assert_not_called()

    def test_server_requested_delay_is_honoured(self, sleep):
        body = b'{"error": {"details": [{"retryDelay": "32s"}]}}'
        busy = urllib.error.HTTPError("https://x", 429, "busy", {}, io.BytesIO(body))
        with mock.patch("urllib.request.urlopen", side_effect=[busy, http_response(REPLY)]):
            provider.complete("m", "sys", [{"role": "user", "text": "hi"}], [])
        sleep.assert_called_once_with(32.0)

    def test_transient_errors_retry(self, sleep):
        responses = [http_error(503), http_error(429), http_response(REPLY)]
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            reply = provider.complete("m", "sys", [{"role": "user", "text": "hi"}], [])
        self.assertEqual(reply["text"], "Reading it.")
        self.assertEqual(sleep.call_count, 2)

    def test_permanent_errors_raise_immediately(self, sleep):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(400)]):
            with self.assertRaisesRegex(RuntimeError, "400.*server says no"):
                provider.complete("m", "sys", [{"role": "user", "text": "hi"}], [])
        sleep.assert_not_called()

    def test_model_info_declares_capabilities(self, sleep):
        info = provider.model_info("m")
        self.assertTrue(info["supports_tools"])
        self.assertGreater(info["context_window"], info["max_output_tokens"])


if __name__ == "__main__":
    unittest.main()
