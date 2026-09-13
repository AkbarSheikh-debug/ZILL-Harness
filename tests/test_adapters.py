"""Tests for model dispatch and the Anthropic and OpenAI-compatible adapters.

Requests are captured at urllib.request.urlopen, so the exact wire bodies
and headers each adapter sends are asserted without any network access.
"""

import json
import os
import unittest
from unittest import mock

from tests.fake import FakeProvider, call, text
from zill import Harness, provider
from zill.providers import anthropic, gemini, openai_compat

NO_KEYS = {k: "" for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                           "OPENROUTER_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
                           "ZILL_API_KEY", "ZILL_MODEL", "ZILL_BASE_URL")}


class Capture:
    """Stand-in for urlopen: records each Request and replays JSON replies."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        response = mock.MagicMock()
        body = json.dumps(self.replies.pop(0)).encode("utf-8")
        response.__enter__.return_value.read.return_value = body
        return response

    def body(self, index=0):
        return json.loads(self.requests[index].data)

    def header(self, name, index=0):
        return self.requests[index].get_header(name.capitalize())


SCHEMA = {"schema": {"name": "read_file", "description": "Read a file.",
                     "parameters": {"type": "object", "properties": {}, "required": []}}}


@mock.patch.dict(os.environ, NO_KEYS)
class DispatchTests(unittest.TestCase):
    def test_prefixes_and_bare_names_pick_adapters(self):
        cases = {"anthropic:claude-opus-5": (anthropic, "claude-opus-5"),
                 "claude-sonnet-5": (anthropic, "claude-sonnet-5"),
                 "gpt-5": (openai_compat, "gpt-5"),
                 "o3-mini": (openai_compat, "o3-mini"),
                 "ollama:qwen3:8b": (openai_compat, "qwen3:8b"),
                 "openrouter:anthropic/claude-opus-5": (openai_compat, "anthropic/claude-opus-5"),
                 "gemini-3.6-flash": (gemini, "gemini-3.6-flash")}
        for model, (adapter, model_id) in cases.items():
            _, got_adapter, _, _, got_id = provider.resolve(model)
            self.assertIs(got_adapter, adapter, model)
            self.assertEqual(got_id, model_id, model)

    def test_default_model_follows_the_first_available_key(self):
        self.assertEqual(provider.default_model(), provider.DEFAULT_MODEL)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-123456"}):
            self.assertEqual(provider.default_model(), "anthropic:claude-opus-5")
            with mock.patch.dict(os.environ, {"ZILL_MODEL": "openai:gpt-5"}):
                self.assertEqual(provider.default_model(), "openai:gpt-5")

    def test_missing_key_is_a_clear_error(self):
        self.assertEqual(provider.missing_key("anthropic:claude-opus-5"), "ANTHROPIC_API_KEY")
        self.assertIsNone(provider.missing_key("ollama:qwen3"))
        with self.assertRaisesRegex(RuntimeError, "zill setup.*ANTHROPIC_API_KEY"):
            provider.complete("anthropic:claude-opus-5", "sys", [], [])

    def test_local_servers_need_no_key(self):
        capture = Capture({"choices": [{"message": {"content": "hi"}}]})
        with mock.patch("urllib.request.urlopen", capture):
            reply = provider.complete("ollama:qwen3", "sys", [{"role": "user", "text": "yo"}], [])
        self.assertEqual(reply["text"], "hi")
        self.assertEqual(capture.requests[0].full_url, "http://localhost:11434/v1/chat/completions")
        self.assertIsNone(capture.header("Authorization"))

    def test_budget_follows_the_models_window(self):
        harness = Harness(".", model="anthropic:claude-haiku-4-5", persist=False,
                          enable_subagents=False)
        self.assertEqual(harness.budget_tokens, int(200_000 * 0.6))


TOOL_TURN = [{"role": "user", "text": "read both"},
             {"role": "assistant", "text": "", "tool_calls": [
                 {"id": "t1", "name": "read_file", "args": {"path": "a"}},
                 {"id": "t2", "name": "read_file", "args": {"path": "b"}}]},
             {"role": "tool", "id": "t1", "name": "read_file", "text": "A"},
             {"role": "tool", "id": "t2", "name": "read_file", "text": "B"}]


@mock.patch.dict(os.environ, {**NO_KEYS, "ANTHROPIC_API_KEY": "sk-ant-test-123456"})
class AnthropicTests(unittest.TestCase):
    REPLY = {"content": [{"type": "thinking", "thinking": "", "signature": "sig-xyz"},
                         {"type": "text", "text": "Reading."},
                         {"type": "tool_use", "id": "toolu_1", "name": "read_file",
                          "input": {"path": "a"}}],
             "stop_reason": "tool_use",
             "usage": {"input_tokens": 10, "cache_read_input_tokens": 90,
                       "cache_creation_input_tokens": 5, "output_tokens": 7}}

    def test_request_shape_headers_and_caching(self):
        capture = Capture(self.REPLY)
        with mock.patch("urllib.request.urlopen", capture):
            reply = provider.complete("anthropic:claude-opus-5", "SYSTEM", TOOL_TURN, [SCHEMA])
        body = capture.body()
        self.assertEqual(capture.requests[0].full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(capture.header("x-api-key"), "sk-ant-test-123456")
        self.assertEqual(capture.header("anthropic-version"), anthropic.VERSION)
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["tools"][0]["input_schema"]["type"], "object")
        self.assertNotIn("temperature", body)
        # Both tool results travel in one user turn, matched by tool_use_id.
        results = body["messages"][-1]
        self.assertEqual(results["role"], "user")
        self.assertEqual([b["tool_use_id"] for b in results["content"]], ["t1", "t2"])
        self.assertEqual(reply["text"], "Reading.")
        self.assertEqual(reply["tool_calls"], [{"id": "toolu_1", "name": "read_file",
                                                "args": {"path": "a"}}])
        self.assertEqual(reply["usage"], {"input": 105, "output": 7})

    def test_thinking_blocks_are_replayed_verbatim_through_the_loop(self):
        capture = Capture(self.REPLY, {"content": [{"type": "text", "text": "done"}],
                                       "stop_reason": "end_turn", "usage": {}})
        with mock.patch("urllib.request.urlopen", capture):
            harness = Harness(".", model="anthropic:claude-opus-5", persist=False,
                              enable_subagents=False)
            with mock.patch.object(harness.tools["read_file"], "run", return_value="A"):
                self.assertEqual(harness.run("read a"), "done")
        replayed = capture.body(1)["messages"][1]
        self.assertEqual(replayed["role"], "assistant")
        self.assertEqual(replayed["content"], self.REPLY["content"])
        self.assertEqual(capture.body(1)["messages"][2]["content"][0]["tool_use_id"], "toolu_1")

    def test_refusal_is_visible(self):
        capture = Capture({"content": [], "stop_reason": "refusal", "usage": {}})
        with mock.patch("urllib.request.urlopen", capture):
            reply = provider.complete("claude-opus-5", "sys", [{"role": "user", "text": "x"}], [])
        self.assertEqual(reply["text"], anthropic.REFUSED)


@mock.patch.dict(os.environ, {**NO_KEYS, "OPENAI_API_KEY": "sk-openai-test-123456"})
class OpenAICompatTests(unittest.TestCase):
    def test_request_and_reply_shapes(self):
        capture = Capture({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "call_9", "type": "function",
             "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}}]}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3}})
        with mock.patch("urllib.request.urlopen", capture):
            reply = provider.complete("openai:gpt-5", "SYSTEM", TOOL_TURN, [SCHEMA])
        body = capture.body()
        self.assertEqual(capture.header("Authorization"), "Bearer sk-openai-test-123456")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "SYSTEM"})
        assistant = body["messages"][2]
        self.assertEqual(json.loads(assistant["tool_calls"][1]["function"]["arguments"]),
                         {"path": "b"})
        self.assertEqual(body["messages"][3], {"role": "tool", "tool_call_id": "t1", "content": "A"})
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertEqual(reply["tool_calls"], [{"id": "call_9", "name": "read_file",
                                                "args": {"path": "a"}}])
        self.assertEqual(reply["usage"], {"input": 12, "output": 3})

    def test_unparseable_arguments_do_not_crash_the_loop(self):
        capture = Capture(
            {"choices": [{"message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{not json"}}]}}]},
            {"choices": [{"message": {"content": "recovered"}}]})
        with mock.patch("urllib.request.urlopen", capture):
            harness = Harness(".", model="openai:gpt-5", persist=False, enable_subagents=False)
            self.assertEqual(harness.run("go"), "recovered")
        result = [m for m in harness.messages if m["role"] == "tool"][0]["text"]
        self.assertTrue(result.startswith("ERROR: TypeError"))


class ProviderSwitchTests(unittest.TestCase):
    def test_a_session_can_continue_on_another_provider(self):
        with FakeProvider(call("list_files"), text("done")):
            harness = Harness(".", persist=False, enable_subagents=False)
            harness.run("list")
        wire = anthropic._to_wire(harness.messages)
        tool_use = wire[1]["content"][0]
        self.assertEqual(tool_use["type"], "tool_use")
        self.assertEqual(wire[2]["content"][0]["tool_use_id"], tool_use["id"])


if __name__ == "__main__":
    unittest.main()
