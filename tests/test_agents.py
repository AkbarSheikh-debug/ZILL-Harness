"""Tests for sub-agents and the fleet."""

import glob
import os
import tempfile
import time
import unittest

from tests.fake import FakeProvider, call, text
from zill import Harness, run_fleet, session
from zill.subagent import DEPTH_LIMIT, subagent_tool


class SubagentTests(unittest.TestCase):
    def test_depth_limit_refuses(self):
        spawn = subagent_tool(lambda depth: self.fail("must not build a child"), depth=2)
        self.assertEqual(spawn.run(task="anything"), DEPTH_LIMIT)

    def test_child_reports_back_without_hijacking_the_session(self):
        with tempfile.TemporaryDirectory() as workdir:
            script = [call("spawn_agent", task="write b.txt"),        # parent
                      call("write_file", path="b.txt", content="b"),  # child
                      text("child done"),                             # child
                      text("parent done")]                            # parent
            with FakeProvider(*script):
                parent = Harness(workdir)
                self.assertEqual(parent.run("delegate"), "parent done")
            result = [m for m in parent.messages if m["role"] == "tool"][0]
            self.assertEqual(result["text"], "child done")
            self.assertTrue(os.path.exists(os.path.join(workdir, "b.txt")))
            # parent.workdir is realpath'd (macOS /private, Windows 8.3 names).
            sessions = glob.glob(os.path.join(parent.workdir, session.SESSION_DIR, "*.jsonl"))
            self.assertEqual(sessions, [parent.session_path])


class FleetTests(unittest.TestCase):
    def test_results_keep_input_order_and_isolate_failures(self):
        class Stub:
            def __init__(self, workdir):
                self.workdir = workdir

            def run(self, task):
                if task == "fail":
                    raise RuntimeError("nope")
                time.sleep(float(task))
                return f"{self.workdir} finished"

        jobs = [{"name": "slow", "workdir": "a", "task": "0.2"},
                {"name": "broken", "workdir": "b", "task": "fail"},
                {"name": "fast", "workdir": "c", "task": "0"}]
        results = run_fleet(jobs, Stub)
        self.assertEqual([r["name"] for r in results], ["slow", "broken", "fast"])
        self.assertEqual([r["ok"] for r in results], [True, False, True])
        self.assertEqual(results[1]["report"], "RuntimeError: nope")


if __name__ == "__main__":
    unittest.main()
