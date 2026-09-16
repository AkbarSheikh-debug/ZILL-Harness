"""Tests for the edits and auto modes, the auto-mode reviewer, and effort."""

import json
import os
import tempfile
import unittest
from unittest import mock

from tests.fake import FakeProvider, call, text
from tests.test_adapters import NO_KEYS, SCHEMA, Capture
from zill import Harness, Policy, config, provider, settings, tool
from zill.cli import build_parser
from zill.providers import anthropic
from zill.security import MODES
from zill.tools import core_tools


def bash(command):
    return {"name": "bash", "args": {"command": command}}


class ModeDecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tools = {t.name: t for t in core_tools(self._tmp.name)}
        self.asked = []

    def tearDown(self):
        self._tmp.cleanup()

    def approver(self, answer):
        return lambda c, reason: self.asked.append(reason) or answer

    def test_modes_are_ordered_strictest_first(self):
        self.assertEqual(MODES, ("read-only", "safe", "edits", "auto", "yolo"))
        self.assertEqual(settings.STRICTNESS["edits"], 2)

    def test_edits_mode_runs_builtin_edits_and_asks_for_the_rest(self):
        policy = Policy("edits", approver=self.approver(False))
        write = {"name": "write_file", "args": {"path": "a", "content": "x"}}
        self.assertTrue(policy.decide(write, self.tools["write_file"]).allowed)
        refused = policy.decide(bash("pytest -q"), self.tools["bash"])
        self.assertFalse(refused.allowed)
        self.assertIn("needs approval in edits mode", self.asked[0])

    def test_edits_mode_never_auto_approves_plugin_writes(self):
        @tool("Write somewhere.", risk="write", source="plugin")
        def remote_write():
            return "ok"

        policy = Policy("edits", approver=self.approver(False))
        self.assertFalse(policy.decide({"name": "remote_write", "args": {}}, remote_write).allowed)
        self.assertEqual(len(self.asked), 1)

    def test_auto_mode_runs_what_the_reviewer_clears_and_asks_about_the_rest(self):
        verdicts = iter([None, "it pushes to a remote"])
        reviewed = []
        policy = Policy("auto", approver=self.approver(False),
                        reviewer=lambda c, risk: reviewed.append(risk) or next(verdicts))
        self.assertTrue(policy.decide(bash("pytest -q"), self.tools["bash"]).allowed)
        refused = policy.decide(bash("git push origin main"), self.tools["bash"])
        self.assertEqual(reviewed, ["execute", "network"])
        self.assertFalse(refused.allowed)
        self.assertIn("safety check paused it: it pushes to a remote", self.asked[0])
        self.assertIn("it pushes to a remote", refused.reason)

    def test_auto_mode_never_reviews_destructive_or_tainted_calls(self):
        reviewer = mock.Mock(return_value=None)
        policy = Policy("auto", approver=self.approver(False), reviewer=reviewer)
        self.assertEqual(policy.decide(bash("rm -rf ~"), self.tools["bash"]).risk, "destructive")
        write = {"name": "write_file", "args": {"path": "ZILL.md", "content": "x"}}
        self.assertFalse(policy.decide(write, self.tools["write_file"], ask_reason="tainted").allowed)
        reviewer.assert_not_called()
        self.assertEqual(self.asked, ["tainted"])

    def test_auto_mode_without_a_reviewer_asks(self):
        policy = Policy("auto", approver=self.approver(True))
        self.assertTrue(policy.decide(bash("make"), self.tools["bash"]).needs_approval)

    def test_project_file_may_tighten_auto_but_never_loosen_it(self):
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, ".zill"))
            with open(os.path.join(workdir, config.PROJECT_FILE), "w", encoding="utf-8") as f:
                json.dump({"mode": "edits", "effort": "low"}, f)
            with mock.patch.object(config, "load_user", return_value={"mode": "auto"}):
                resolved = settings.resolve(workdir, model="gemini:gemini-3.6-flash")
            self.assertEqual((resolved["mode"], resolved["effort"]), ("edits", "low"))
            with mock.patch.object(config, "load_user", return_value={"mode": "safe"}):
                resolved = settings.resolve(workdir, model="gemini:gemini-3.6-flash")
            self.assertEqual(resolved["mode"], "safe")
            self.assertTrue(any("edits" in note for note in resolved["notes"]))


class AutoReviewHarnessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = os.path.realpath(self._tmp.name)
        self.events = []

    def tearDown(self):
        self._tmp.cleanup()

    def run_auto(self, *script):
        policy = Policy("auto", approver=lambda c, r: False)
        on_event = lambda kind, payload: self.events.append((kind, payload))  # noqa: E731
        with FakeProvider(*script) as fake:
            harness = Harness(self.workdir, model="gemini:gemini-3.6-flash", policy=policy,
                              on_event=on_event, persist=False, checkpoints=False,
                              extensions=False)
            harness.run("run the tests")
        return harness, fake

    def reviews(self):
        return [payload for kind, payload in self.events if kind == "auto_review"]

    def test_a_cleared_command_runs_and_the_reviewer_sees_no_tool_output(self):
        script = [call("write_file", path="a.txt", content="PAYLOAD-FROM-A-FILE"),
                  call("bash", command="echo hi"), text("SAFE"), text("done")]
        harness, fake = self.run_auto(*script)
        review = fake.requests[2]
        self.assertEqual((review["tools"], review["effort"]), ([], "low"))
        prompt = review["messages"][0]["text"]
        self.assertIn("run the tests", prompt)
        self.assertIn("echo hi", prompt)
        self.assertNotIn("PAYLOAD-FROM-A-FILE", prompt)  # the edit ran without review
        self.assertEqual(self.reviews(), [{"id": mock.ANY, "name": "bash", "risk": "execute",
                                           "concern": None}])
        self.assertIn("hi", harness.messages[4]["text"])
        self.assertEqual(harness.usage["calls"], 4)

    def test_a_risky_verdict_pauses_the_call_for_the_person(self):
        script = [call("bash", command="echo x > ../outside.txt"),
                  text("RISKY: writes outside the project"), text("stopped")]
        harness, _ = self.run_auto(*script)
        self.assertFalse(os.path.exists(os.path.join(self.workdir, "..", "outside.txt")))
        self.assertIn("BLOCKED: the user did not approve bash", harness.messages[2]["text"])
        self.assertEqual(self.reviews()[0]["concern"], "writes outside the project")

    def test_an_unclear_review_fails_closed(self):
        self.run_auto(call("bash", command="echo hi"), text("probably fine?"), text("ok"))
        self.assertEqual(self.reviews()[0]["concern"], "the safety check gave no clear verdict")


@mock.patch.dict(os.environ, {**NO_KEYS, "ANTHROPIC_API_KEY": "sk-ant-test-123456",
                              "OPENAI_API_KEY": "sk-test-123456", "GEMINI_API_KEY": "g-123456",
                              "OPENROUTER_API_KEY": "sk-or-test-123456"})
class EffortTests(unittest.TestCase):
    CLAUDE = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    OPENAI = {"choices": [{"message": {"content": "ok"}}]}
    GEMINI = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    def send(self, model, reply, effort):
        capture = Capture(reply)
        with mock.patch("urllib.request.urlopen", capture):
            provider.complete(model, "sys", [{"role": "user", "text": "hi"}], [SCHEMA],
                              effort=effort)
        return capture.body()

    def test_claude_gets_output_config_effort_where_the_model_has_it(self):
        body = self.send("anthropic:claude-opus-5", self.CLAUDE, "xhigh")
        self.assertEqual(body["output_config"], {"effort": "xhigh"})
        self.assertNotIn("output_config", self.send("anthropic:claude-opus-5", self.CLAUDE, None))
        self.assertIsNone(anthropic.effort_for("claude-haiku-4-5", "high"))
        self.assertEqual(anthropic.effort_for("claude-opus-4-6", "xhigh"), "high")
        self.assertEqual(anthropic.effort_for("claude-opus-4-5", "max"), "high")

    def test_openai_reasoning_models_get_reasoning_effort_and_others_nothing(self):
        self.assertEqual(self.send("openai:gpt-5", self.OPENAI, "max")["reasoning_effort"], "high")
        body = self.send("openrouter:openai/o3", self.OPENAI, "low")
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", self.send("ollama:qwen3", self.OPENAI, "high"))

    def test_gemini_3_gets_a_thinking_level(self):
        body = self.send("gemini:gemini-3.6-flash", self.GEMINI, "medium")
        self.assertEqual(body["generationConfig"]["thinkingConfig"], {"thinkingLevel": "medium"})
        body = self.send("gemini:gemini-2.5-pro", self.GEMINI, "medium")
        self.assertNotIn("thinkingConfig", body["generationConfig"])

    def test_unknown_effort_is_refused(self):
        with self.assertRaisesRegex(ValueError, "effort must be one of"):
            provider.complete("ollama:qwen3", "sys", [], [], effort="extreme")

    def test_harness_sends_its_effort_to_every_turn_and_children(self):
        with tempfile.TemporaryDirectory() as workdir:
            with FakeProvider(call("spawn_agent", task="look"), text("child"),
                              text("parent")) as fake:
                harness = Harness(workdir, model="gemini:gemini-3.6-flash", effort="high",
                                  persist=False, checkpoints=False, extensions=False)
                harness.run("go")
                harness.close()
            harness.set_effort(None)
            with self.assertRaises(ValueError):
                harness.set_effort("extreme")
        self.assertEqual([r["effort"] for r in fake.requests], ["high", "high", "high"])
        self.assertIsNone(harness.effort)

    def test_config_and_cli_accept_effort(self):
        args = build_parser().parse_args(["--effort", "max", "--mode", "auto"])
        self.assertEqual((args.effort, args.mode), ("max", "auto"))
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, ".zill"))
            with open(os.path.join(workdir, config.PROJECT_FILE), "w", encoding="utf-8") as f:
                json.dump({"effort": "extreme"}, f)
            with self.assertRaisesRegex(RuntimeError, "\"effort\" must be one of"):
                config.load_project(workdir)


if __name__ == "__main__":
    unittest.main()
