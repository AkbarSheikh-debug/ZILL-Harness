"""Tests for v0.6: settings, profiles, subcommands, --json, streaming, cost, slash commands."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests.fake import FakeProvider, call, text
from zill import cli, config, cost, settings
from zill.providers import anthropic, gemini, openai_compat

KEY = "gm-test-key-1234567890"
CLEAN = {name: "" for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                                "OPENROUTER_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
                                "ZILL_API_KEY", "ZILL_MODEL", "ZILL_BASE_URL")}


class Env(unittest.TestCase):
    """A temp project and a temp ZILL_HOME with a Gemini key, and nothing else."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = os.path.realpath(os.path.join(self._tmp.name, "project"))
        self.home = os.path.join(self._tmp.name, "home")
        os.makedirs(self.workdir)
        patcher = mock.patch.dict(os.environ, {**CLEAN, "ZILL_HOME": self.home,
                                               "GEMINI_API_KEY": KEY})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def project(self, **data):
        self.write_json(os.path.join(self.workdir, config.PROJECT_FILE), data)

    def user(self, **data):
        self.write_json(config.user_path(), data)

    def cli(self, *argv):
        """Run cli.main and return (exit code, stdout, stderr)."""
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class SettingsTests(Env):
    def test_model_precedence(self):
        self.assertEqual(settings.resolve(self.workdir)["model"], "gemini:gemini-3.6-flash")
        self.user(model="user:model")
        self.assertEqual(settings.resolve(self.workdir)["model"], "user:model")
        self.project(model="project:model")
        self.assertEqual(settings.resolve(self.workdir)["model"], "project:model")
        with mock.patch.dict(os.environ, {"ZILL_MODEL": "env:model"}):
            self.assertEqual(settings.resolve(self.workdir)["model"], "env:model")
            self.assertEqual(settings.resolve(self.workdir, model="flag:m")["model"], "flag:m")

    def test_a_project_can_only_tighten_the_mode(self):
        self.project(mode="yolo")
        resolved = settings.resolve(self.workdir)
        self.assertEqual(resolved["mode"], "safe")
        self.assertIn("may only tighten", resolved["notes"][0])
        self.project(mode="read-only")
        self.assertEqual(settings.resolve(self.workdir, headless=True)["mode"], "read-only")
        self.assertEqual(settings.resolve(self.workdir, mode="yolo")["mode"], "yolo")

    def test_bad_config_is_a_clear_error(self):
        self.user(colour="blue")
        with self.assertRaisesRegex(RuntimeError, "unknown keys"):
            settings.resolve(self.workdir)
        self.user(prices={"gemini:x": [1]})
        with self.assertRaisesRegex(RuntimeError, "prices"):
            settings.resolve(self.workdir)
        self.user()
        self.project(profile="wizard")
        with self.assertRaisesRegex(RuntimeError, "profile"):
            settings.resolve(self.workdir)

    def test_reviewer_profile_narrows_tools_and_mode(self):
        harness = settings.make_harness(settings.resolve(self.workdir, profile="reviewer"),
                                        persist=False)
        self.assertEqual(harness.policy.mode, "read-only")
        self.assertNotIn("write_file", harness.tools)
        self.assertNotIn("bash", harness.tools)
        self.assertIn("read_file", harness.tools)
        self.assertIn("Profile: reviewer", harness.system)


class CommandTests(Env):
    def test_doctor_reports_without_printing_keys(self):
        code, out, _ = self.cli("doctor", "-d", self.workdir, "--json")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(report["ok"])
        self.assertNotIn(KEY, out)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            code, out, _ = self.cli("doctor", "-d", self.workdir)
        self.assertEqual(code, 1)
        self.assertIn("fail  model", out)

    def test_inspect_shows_tools_with_risk_and_never_the_key(self):
        code, out, _ = self.cli("inspect", "-d", self.workdir, "--json", "--profile", "reviewer")
        info = json.loads(out)
        self.assertEqual((code, info["mode"], info["key"]), (0, "read-only", "set"))
        self.assertIn({"name": "grep", "source": "builtin", "risk": "read"}, info["tools"])
        self.assertNotIn(KEY, out)
        self.assertEqual(os.listdir(self.workdir), [])  # inspect writes nothing

    def test_run_json_prints_one_result_object(self):
        with FakeProvider(call("write_file", path="a.txt", content="hi"), text("all done")):
            code, out, err = self.cli("run", "make a.txt", "-d", self.workdir, "--json")
        result = json.loads(out)
        self.assertEqual((code, result["ok"], result["result"]), (0, True, "all done"))
        self.assertEqual(result["usage"]["calls"], 2)
        self.assertIsNone(result["cost_usd"])  # no documented price for this Gemini model
        self.assertEqual(err, "")

    def test_json_errors_are_json_too(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            code, out, _ = self.cli("run", "x", "-d", self.workdir, "--json")
        self.assertEqual(code, 1)
        self.assertIn("zill setup", json.loads(out)["error"])

    def test_sessions_lists_runs(self):
        with FakeProvider(text("hello")):
            self.cli("run", "say hello", "-d", self.workdir, "--json")
        code, out, _ = self.cli("sessions", "-d", self.workdir, "--json")
        rows = json.loads(out)
        self.assertEqual((code, len(rows), rows[0]["task"]), (0, 1, "say hello"))

    def test_fleet_runs_jobs_from_a_file(self):
        jobs = os.path.join(self._tmp.name, "jobs.json")
        self.write_json(jobs, [{"name": "one", "workdir": "a", "task": "t1"},
                               {"name": "two", "workdir": "b", "task": "t2"}])
        with FakeProvider(text("finished"), text("finished")):
            code, out, _ = self.cli("fleet", jobs, "--workers", "1", "--json")
        results = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([(r["name"], r["ok"], r["report"]) for r in results],
                         [("one", True, "finished"), ("two", True, "finished")])

    def test_resume_subcommand_continues_the_latest_session(self):
        with FakeProvider(text("first")):
            self.cli("run", "one", "-d", self.workdir, "--json")
        with FakeProvider(text("second")) as fake:
            self.cli("resume", "two", "-d", self.workdir, "--json")
        self.assertEqual([m["text"] for m in fake.requests[0]["messages"]], ["one", "first", "two"])


class SSE:
    """Stand-in for urlopen that streams server-sent events."""

    def __init__(self, events, done=False):
        lines = []
        for event in events:
            lines += [b"event: x\n", f"data: {json.dumps(event)}\n".encode(), b"\n"]
        self.lines = lines + ([b"data: [DONE]\n"] if done else [])
        self.request = None

    def __call__(self, request, timeout=None):
        self.request = request
        response = mock.MagicMock()
        response.__enter__.return_value.__iter__.return_value = iter(self.lines)
        return response


class StreamingTests(unittest.TestCase):
    def collect(self, adapter, sse, messages=(), tools=()):
        pieces = []
        with mock.patch("urllib.request.urlopen", sse):
            reply = adapter.complete("m", "sys", list(messages) or [{"role": "user", "text": "hi"}],
                                     list(tools), base="https://x", key="k",
                                     on_text=pieces.append)
        return reply, pieces

    def test_gemini_stream(self):
        sse = SSE([{"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
                   {"candidates": [{"content": {"parts": [
                       {"text": "lo"},
                       {"functionCall": {"id": "c1", "name": "grep", "args": {"regex": "x"}},
                        "thoughtSignature": "sig"}]}}],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2}}])
        reply, pieces = self.collect(gemini, sse)
        self.assertIn(":streamGenerateContent?alt=sse", sse.request.full_url)
        self.assertEqual((pieces, reply["text"]), (["Hel", "lo"], "Hello"))
        self.assertEqual(reply["tool_calls"][0]["signature"], "sig")
        self.assertEqual(reply["usage"], {"input": 5, "output": 2})

    def test_anthropic_stream_rebuilds_blocks_exactly(self):
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "hmm"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "signature_delta", "signature": "SIG"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Go"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block":
                {"type": "tool_use", "id": "toolu_1", "name": "grep", "input": {}}},
            {"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta", "partial_json": "{\"regex\": "}},
            {"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta", "partial_json": "\"x\"}"}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
             "usage": {"output_tokens": 4}},
            {"type": "message_stop"}]
        sse = SSE(events)
        reply, pieces = self.collect(anthropic, sse)
        self.assertTrue(json.loads(sse.request.data)["stream"])
        self.assertEqual(pieces, ["Go"])
        self.assertEqual(reply["provider_data"]["anthropic"], [
            {"type": "thinking", "thinking": "hmm", "signature": "SIG"},
            {"type": "text", "text": "Go"},
            {"type": "tool_use", "id": "toolu_1", "name": "grep", "input": {"regex": "x"}}])
        self.assertEqual(reply["tool_calls"], [{"id": "toolu_1", "name": "grep",
                                                "args": {"regex": "x"}}])
        self.assertEqual(reply["usage"], {"input": 9, "output": 4})

    def test_anthropic_stream_error_raises(self):
        sse = SSE([{"type": "error", "error": {"type": "overloaded_error"}}])
        with self.assertRaisesRegex(RuntimeError, "overloaded"):
            self.collect(anthropic, sse)

    def test_openai_stream_merges_tool_call_fragments(self):
        sse = SSE([
            {"choices": [{"delta": {"content": "Look"}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "grep", "arguments": "{\"re"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "gex\": \"x\"}"}}]}}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}], done=True)
        reply, pieces = self.collect(openai_compat, sse)
        body = json.loads(sse.request.data)
        self.assertEqual((body["stream"], body["stream_options"]), (True, {"include_usage": True}))
        self.assertEqual((pieces, reply["text"]), (["Look"], "Look"))
        self.assertEqual(reply["tool_calls"], [{"id": "call_1", "name": "grep",
                                                "args": {"regex": "x"}}])
        self.assertEqual(reply["usage"], {"input": 7, "output": 3})

    def test_terminal_redacts_a_key_split_across_fragments(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-split-secret-987654"}), \
                redirect_stdout(io.StringIO()) as out:
            for piece in ["your key is sk-spl", "it-secret-98", "7654\nand more"]:
                cli.print_event("text_delta", {"text": piece})
            cli.print_event("assistant", {"text": "ignored", "streamed": True})
        self.assertEqual(out.getvalue(), "your key is [REDACTED]\nand more\n")


class CostAndSlashTests(Env):
    def test_cost_uses_documented_prices_only(self):
        usage = {"calls": 1, "input": 1_000_000, "output": 100_000}
        self.assertAlmostEqual(cost.estimate("anthropic:claude-opus-5", usage), 7.5)
        self.assertIsNone(cost.estimate("gemini:gemini-3.6-flash", usage))
        self.assertAlmostEqual(
            cost.estimate("gemini-3.6-flash", usage, {"gemini:gemini-3.6-flash": [0.3, 2.5]}), 0.55)
        self.assertIn("price unknown", cost.describe("gemini-3.6-flash", usage))

    def slash(self, harness, *lines):
        with redirect_stdout(io.StringIO()) as out:
            results = [cli._slash(harness, line) for line in lines]
        return results, out.getvalue()

    def test_slash_commands(self):
        harness = settings.make_harness(settings.resolve(self.workdir), persist=False)
        with FakeProvider(text("hi")):
            harness.run("hello")
        results, out = self.slash(harness, "/help", "/cost", "/mode read-only", "/mode nope",
                                  "/model anthropic:claude-opus-5", "/todo", "/skills",
                                  "/clear", "/bogus", "/exit")
        self.assertEqual(results, [True] * 9 + [False])
        self.assertIn("/compact", out)
        self.assertIn("1 model calls", out)
        self.assertEqual(harness.policy.mode, "read-only")
        self.assertIn("mode must be one of", out)
        self.assertIn("needs ANTHROPIC_API_KEY", out)  # refused: no key for that provider
        self.assertEqual(harness.model, "gemini:gemini-3.6-flash")
        self.assertEqual((harness.messages, harness.session_path), ([], None))
        self.assertIn("unknown command /bogus", out)

    def test_context_command_breaks_down_the_window(self):
        harness = settings.make_harness(settings.resolve(self.workdir), persist=False)
        reply = {**text("hi"), "usage": {"input": 4321, "output": 5}}
        with FakeProvider(reply):
            harness.run("hello " * 400)
        used = harness.context_usage()
        self.assertEqual(used["window"], 1_048_576)
        self.assertEqual(used["total"], used["system"] + used["tools"] + used["messages"])
        self.assertGreater(used["messages"], 400)
        self.assertEqual(used["reported"], 4321)
        _, out = self.slash(harness, "/context")
        self.assertIn("of 1,048,576 tokens", out)
        self.assertIn("messages (2)", out)
        self.assertIn("4,321 input tokens", out)
        harness.clear()
        self.assertEqual(harness.context_usage()["reported"], 0)

    def test_model_picker_lists_and_switches_by_number(self):
        harness = settings.make_harness(settings.resolve(self.workdir), persist=False)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-123456"}):
            _, out = self.slash(harness, "/model")  # stdin is not a tty: list only
            self.assertIn("* 1. gemini:gemini-3.6-flash", out)
            self.assertIn("2. anthropic:claude-opus-5\n", out)
            self.assertIn("(needs OPENAI_API_KEY)", out)
            _, out = self.slash(harness, "/model 99", "/model 3")
            self.assertIn("choose a number from 1 to", out)
            self.assertIn("needs OPENAI_API_KEY", out)
            self.assertEqual(harness.model, "gemini:gemini-3.6-flash")
            self.slash(harness, "/model ollama:qwen3")
            _, out = self.slash(harness, "/model")  # numbers stay put; the custom model is last
            self.assertIn("  1. gemini:gemini-3.6-flash", out)
            self.assertIn("* 5. ollama:qwen3", out)
            self.slash(harness, "/model 2")
        self.assertEqual(harness.model, "anthropic:claude-opus-5")
        self.assertEqual(harness.budget_tokens,
                         int(anthropic.model_info("claude-opus-5")["context_window"] * 0.6))

    def test_model_switch_warns_when_the_conversation_is_past_the_new_budget(self):
        harness = settings.make_harness(settings.resolve(self.workdir), persist=False)
        harness.messages = [{"role": "user", "text": "x" * 400_000}]  # ~100k tokens
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-deep-test-123456"}):
            _, out = self.slash(harness, "/model deepseek:deepseek-chat")
        self.assertIn("will be summarised before the next turn", out)

    def test_compact_command_summarises(self):
        harness = settings.make_harness(settings.resolve(self.workdir), persist=False)
        with FakeProvider(*([call("list_files")] * 4 + [text("done"), text("SUMMARY")])):
            harness.run("work")
            _, out = self.slash(harness, "/compact")
        self.assertIn("compacted 10 messages to", out)
        self.assertIn("SUMMARY", harness.messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
