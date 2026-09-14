"""Scenario tests for the agent loop, driven through a real Harness."""

import os
import tempfile
import unittest

from tests.fake import FakeProvider, call, text
from zill import Harness, Policy, tool


def tool_results(harness):
    """Return the tool messages of a harness transcript."""
    return [m for m in harness.messages if m["role"] == "tool"]


class LoopScenarios(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def harness(self, **kwargs):
        kwargs.setdefault("persist", False)
        kwargs.setdefault("enable_subagents", False)
        return Harness(self.workdir, **kwargs)

    def test_normal_tool_loop(self):
        with FakeProvider(call("write_file", path="a.txt", content="hi"), text("done")) as fake:
            harness = self.harness()
            self.assertEqual(harness.run("make a.txt"), "done")
        with open(os.path.join(self.workdir, "a.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "hi")
        assistant = harness.messages[1]
        (result,) = tool_results(harness)
        self.assertEqual(result["id"], assistant["tool_calls"][0]["id"])
        self.assertTrue(result["id"])
        self.assertIn("write_file", fake.requests[0]["tools"])

    def test_blocked_tool_is_a_result(self):
        with FakeProvider(call("write_file", path="a.txt", content="x"), text("ok")):
            harness = self.harness(policy=Policy("read-only"))
            harness.run("try to write")
        self.assertTrue(tool_results(harness)[0]["text"].startswith("BLOCKED:"))
        self.assertFalse(os.path.exists(os.path.join(self.workdir, "a.txt")))

    def test_unknown_tool(self):
        with FakeProvider(call("teleport"), text("ok")):
            harness = self.harness()
            harness.run("go")
        self.assertEqual(tool_results(harness)[0]["text"], "ERROR: unknown tool teleport")

    def test_tool_exception_becomes_error(self):
        @tool("Always fails.")
        def explode():
            raise ValueError("boom")

        with FakeProvider(call("explode"), text("recovered")):
            harness = self.harness(extra_tools=[explode])
            self.assertEqual(harness.run("go"), "recovered")
        self.assertEqual(tool_results(harness)[0]["text"], "ERROR: ValueError: boom")

    def test_path_escape_is_refused(self):
        with FakeProvider(call("read_file", path="../outside.txt"), text("ok")):
            harness = self.harness()
            harness.run("peek")
        self.assertIn("PermissionError", tool_results(harness)[0]["text"])

    def test_turn_limit_forces_a_toolless_wrap_up(self):
        replies = [call("list_files"), call("list_files"), text("wrapped")]
        with FakeProvider(*replies) as fake:
            harness = self.harness(max_turns=2)
            self.assertEqual(harness.run("loop forever"), "wrapped")
        self.assertEqual(fake.requests[-1]["tools"], [])

    def test_repeated_identical_calls_warn_then_stop_the_run(self):
        @tool("Report the build status.", risk="read")
        def status():
            return "build: failing"

        events = []
        replies = [call("status")] * 5 + [text("I was stuck")]
        with FakeProvider(*replies) as fake:
            harness = self.harness(extra_tools=[status],
                                   on_event=lambda kind, payload: events.append((kind, payload)))
            self.assertEqual(harness.run("loop forever"), "I was stuck")
        results = [m["text"] for m in tool_results(harness)]
        self.assertNotIn("[ZILL:", results[1])
        self.assertIn("returned this exact result 3 times", results[2])
        self.assertEqual(fake.requests[-1]["tools"], [])  # a tool-less wrap-up call
        self.assertIn("Stopped: status kept returning", harness.messages[-2]["text"])
        detected = [p for kind, p in events if kind == "loop_detected"]
        self.assertEqual([(p["count"], p["stopped"]) for p in detected],
                         [(3, False), (4, False), (5, True)])

    def test_calls_that_make_progress_are_not_loops(self):
        replies = [call("write_file", path="a.txt", content=str(n)) for n in range(6)]
        with FakeProvider(*replies, text("done")):
            harness = self.harness()
            self.assertEqual(harness.run("count"), "done")
        self.assertFalse(any("[ZILL:" in m["text"] for m in tool_results(harness)))

    def test_system_prompt_names_the_real_shell(self):
        from zill.tools import SHELL
        self.assertIn(SHELL, self.harness().system)


if __name__ == "__main__":
    unittest.main()
