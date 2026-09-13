"""The v1.0 integration test: every capability in one run, checked end to end.

One persistent, safe-mode session in a real project directory uses builtin
tools, a skill, an approved MCP server, an enabled plugin, a connector, a
sub-agent that uses the connector, memory and a file write. Then the audit
log must show every tool's source and decision, the session must resume,
memory must reach a fresh harness, and checkpoints must cover the changes.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests.fake import FakeProvider, call, text
from zill import Harness, Policy, cli
from zill.checkpoints import Checkpoints

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "mcp_server.py")


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = os.path.realpath(os.path.join(self._tmp.name, "project"))
        home = mock.patch.dict(os.environ, {"ZILL_HOME": os.path.join(self._tmp.name, "home")})
        home.start()
        self.addCleanup(home.stop)
        skill = os.path.join(self.workdir, "skills", "release-notes")
        os.makedirs(skill)
        with open(os.path.join(skill, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\ndescription: How to write release notes\n---\nLead with what changed.")
        plugins = os.path.join(self.workdir, ".zill", "plugins")
        shutil.copytree(os.path.join(REPO, "examples", "plugins", "hello_plugin"),
                        os.path.join(plugins, "hello"))
        shutil.copytree(os.path.join(REPO, "examples", "connectors", "local_notes"),
                        os.path.join(plugins, "local_notes"))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["mcp", "add", "-d", self.workdir, "calc", "--",
                                       sys.executable, FIXTURE]), 0)
            for name in ("hello", "local_notes"):
                self.assertEqual(cli.main(["plugin", "enable", name, "-d", self.workdir,
                                           "--yes"]), 0)

    def test_every_capability_in_one_safe_persistent_run(self):
        approvals = []

        def approve(tool_call, reason):
            approvals.append(tool_call["name"])
            return True

        script = [
            call("use_skill", name="release-notes"),
            call("mcp__calc__add", a=20, b=22),
            call("plugin__hello__hello", name="Ada"),
            call("connector__local_notes__create_note", name="plan", text="- ship v1.0"),
            call("spawn_agent", task="Read the note called plan and report what it says."),
            call("connector__local_notes__read_note", name="plan"),       # the sub-agent
            text("The plan says: ship v1.0"),                              # the sub-agent
            call("remember", note="v1.0 integration run happened"),
            call("write_file", path="report.md", content="sum=42"),
            text("all done"),
        ]
        with FakeProvider(*script):
            harness = Harness(self.workdir, policy=Policy("safe", approver=approve))
            self.addCleanup(harness.close)
            self.assertEqual(harness.notes, [])
            self.assertEqual(harness.run("exercise everything"), "all done")

        results = [m["text"] for m in harness.messages if m["role"] == "tool"]
        self.assertIn("Lead with what changed.", results[0])
        self.assertEqual(results[1:], ["42", "Hello, Ada! (from the hello plugin)",
                                       "saved note plan", "The plan says: ship v1.0",
                                       "Remembered in ZILL.md", "Wrote 6 chars to report.md"])

        # Approval was asked exactly for the calls that change state or execute.
        self.assertEqual(approvals, ["mcp__calc__add", "connector__local_notes__create_note",
                                     "remember", "write_file"])

        # The audit log shows the source, risk and decision of every tool used.
        with open(os.path.join(self.workdir, ".zill", "audit.jsonl"), encoding="utf-8") as f:
            audit = [json.loads(line) for line in f]
        self.assertEqual([(e["tool"], e["source"], e["risk"], e["decision"]) for e in audit], [
            ("use_skill", "builtin", "read", "allowed"),
            ("mcp__calc__add", "mcp", "execute", "approved"),
            ("plugin__hello__hello", "plugin", "read", "allowed"),
            ("connector__local_notes__create_note", "connector", "write", "approved"),
            ("connector__local_notes__read_note", "connector", "read", "allowed"),
            ("spawn_agent", "builtin", "read", "allowed"),
            ("remember", "builtin", "write", "approved"),
            ("write_file", "builtin", "write", "approved"),
        ])
        child = audit[4]
        self.assertTrue(child["session"].endswith("(sub-agent)"))
        self.assertTrue(all(e["status"] == "ok" for e in audit))

        # State on disk: the connector's note, the report, memory, checkpoints.
        with open(os.path.join(self.workdir, "notes", "plan.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "- ship v1.0")
        with open(os.path.join(self.workdir, "report.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "sum=42")
        store = Checkpoints(self.workdir)
        if store.available:
            self.assertGreaterEqual(len(store.list()), 2)

        # The session resumes intact, and memory reaches a fresh harness.
        fresh = Harness(self.workdir, persist=True)
        self.addCleanup(fresh.close)
        self.assertTrue(fresh.resume())
        self.assertEqual(len(fresh.messages), len(harness.messages))
        self.assertIn("v1.0 integration run happened", fresh.system)
        self.assertIn("- release-notes: How to write release notes", fresh.system)


if __name__ == "__main__":
    unittest.main()
