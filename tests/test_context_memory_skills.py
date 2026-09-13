"""Tests for compaction, project memory, and on-demand skills."""

import os
import tempfile
import unittest

from tests.fake import FakeProvider, call, text
from zill import Harness, context, skills


class ContextTests(unittest.TestCase):
    def test_under_budget_returns_the_same_list(self):
        messages = [{"role": "user", "text": "hi"}]
        self.assertIs(context.compact("m", messages, budget_tokens=1000), messages)

    def test_over_budget_summarises_old_turns(self):
        messages = []
        for i in range(10):
            messages.append({"role": "assistant", "text": "x" * 400,
                             "tool_calls": [{"id": f"c{i}", "name": "grep", "args": {}}]})
            messages.append({"role": "tool", "id": f"c{i}", "name": "grep", "text": "y" * 400})
        with FakeProvider(text("SUMMARY")) as fake:
            compacted = context.compact("m", messages, budget_tokens=100)
        self.assertTrue(compacted[0]["text"].startswith(context.HEADER))
        self.assertIn("SUMMARY", compacted[0]["text"])
        self.assertNotEqual(compacted[1]["role"], "tool")
        self.assertEqual(compacted[1:], messages[-len(compacted) + 1:])
        self.assertEqual(fake.requests[0]["tools"], [])


class MemoryAndSkillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_memory_persists_into_a_fresh_harness(self):
        with FakeProvider(call("remember", note="tests use unittest"), text("ok")):
            Harness(self.workdir, persist=False, enable_subagents=False).run("note it")
        fresh = Harness(self.workdir, persist=False, enable_subagents=False)
        self.assertIn("tests use unittest", fresh.system)

    def test_skills_are_cataloged_and_loaded_on_demand(self):
        folder = os.path.join(self.workdir, skills.SKILLS_DIR, "voice")
        os.makedirs(folder)
        with open(os.path.join(folder, skills.SKILL_FILE), "w", encoding="utf-8") as f:
            f.write("---\ndescription: Write in a warm voice\n---\nFULL SKILL BODY")
        with FakeProvider(call("use_skill", name="voice"), call("use_skill", name="nope"),
                          text("ok")):
            harness = Harness(self.workdir, persist=False, enable_subagents=False)
            harness.run("write copy")
        self.assertIn("- voice: Write in a warm voice", harness.system)
        self.assertNotIn("FULL SKILL BODY", harness.system)
        loaded, missing = [m["text"] for m in harness.messages if m["role"] == "tool"]
        self.assertIn("FULL SKILL BODY", loaded)
        self.assertTrue(missing.startswith("ERROR: no skill named nope"))


if __name__ == "__main__":
    unittest.main()
