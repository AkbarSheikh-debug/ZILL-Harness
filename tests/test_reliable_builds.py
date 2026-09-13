"""Tests for v0.5: the verify loop, checkpoints and undo, the todo tool, and hooks."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from tests.fake import FakeProvider, call, text
from zill import Harness, Policy, cli, config
from zill.checkpoints import Checkpoints

PYTHON = f'"{sys.executable}"'
NEEDS_GIT = unittest.skipUnless(shutil.which("git"), "git is not installed")


class Workdir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = os.path.realpath(self._tmp.name)
        self.events = []

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, content):
        path = os.path.join(self.workdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def read(self, name):
        with open(os.path.join(self.workdir, name), encoding="utf-8") as f:
            return f.read()

    def exists(self, name):
        return os.path.exists(os.path.join(self.workdir, name))

    def harness(self, **kwargs):
        kwargs.setdefault("persist", False)
        kwargs.setdefault("enable_subagents", False)
        kwargs.setdefault("on_event", lambda kind, payload: self.events.append((kind, payload)))
        return Harness(self.workdir, **kwargs)


class VerifyTests(Workdir):
    CHECK = "import sys; sys.exit(0 if open('answer.txt').read() == '42' else 1)"

    def setUp(self):
        super().setUp()
        self.write("check.py", f"{self.CHECK}\nprint('answer is wrong')\n")
        self.verify = f"{PYTHON} check.py"

    def test_failing_check_goes_back_to_the_model_until_it_passes(self):
        script = [call("write_file", path="answer.txt", content="41"), text("done"),
                  call("write_file", path="answer.txt", content="42"), text("fixed")]
        with FakeProvider(*script) as fake:
            self.assertEqual(self.harness(verify=self.verify).run("answer"), "fixed")
        feedback = fake.requests[2]["messages"][-1]["text"]
        self.assertIn("Verification failed", feedback)
        self.assertIn("exited 1", feedback)
        self.assertEqual([p["exit"] for k, p in self.events if k == "verify"], [1, 0])

    def test_gives_up_after_the_fix_rounds_with_a_clear_note(self):
        script = [text("done")] * 4
        with FakeProvider(*script):
            result = self.harness(verify=self.verify).run("answer")
        self.assertIn("Verification still failing after 3 fix rounds", result)

    def test_verify_goes_through_policy(self):
        with FakeProvider(text("done")):
            result = self.harness(verify=self.verify,
                                  policy=Policy("yolo", dry_run=True)).run("answer")
        self.assertIn("Verification not run: dry-run mode", result)
        with FakeProvider(text("done")):
            result = self.harness(verify="rm -rf ~").run("x")
        self.assertIn("deny pattern", result)

    def test_project_file_supplies_verify_and_bad_files_are_clear_errors(self):
        self.write(".zill/project.json", json.dumps({"verify": self.verify}))
        self.assertEqual(self.harness().verify, self.verify)
        self.assertIsNone(self.harness(verify="").verify)
        self.write(".zill/project.json", json.dumps({"hooks": [{"when": "during", "run": "x"}]}))
        with self.assertRaisesRegex(RuntimeError, "hooks\\[0\\]"):
            config.load_project(self.workdir)

    def test_cli_exposes_verify(self):
        self.assertEqual(cli.build_parser().parse_args(["--verify", "pytest"]).verify, "pytest")


@NEEDS_GIT
class CheckpointTests(Workdir):
    def test_undo_walks_back_one_change_at_a_time(self):
        script = [call("write_file", path="a.txt", content="v1"),
                  call("write_file", path="a.txt", content="v2"),
                  call("write_file", path="b.txt", content="new"), text("done")]
        with FakeProvider(*script):
            self.harness().run("edit")
        store = Checkpoints(self.workdir)
        store.restore()
        self.assertEqual((self.read("a.txt"), self.exists("b.txt")), ("v2", False))
        store.restore()
        self.assertEqual(self.read("a.txt"), "v1")
        store.restore()
        self.assertFalse(self.exists("a.txt"))
        with self.assertRaisesRegex(RuntimeError, "nothing to undo"):
            store.restore()

    def test_a_restore_can_itself_be_restored(self):
        with FakeProvider(call("write_file", path="a.txt", content="keep me"), text("done")):
            self.harness().run("edit")
        store = Checkpoints(self.workdir)
        store.restore()
        self.assertFalse(self.exists("a.txt"))
        restore_entry = next(ref for ref, _, label in store.list() if label.startswith("restore:"))
        store.restore(restore_entry)
        self.assertEqual(self.read("a.txt"), "keep me")

    def test_the_users_own_git_repository_is_untouched(self):
        subprocess.run(["git", "init", "-q"], cwd=self.workdir, check=True)
        with FakeProvider(call("write_file", path="a.txt", content="x"), text("done")):
            self.harness().run("edit")
        commits = subprocess.run(["git", "rev-list", "--all"], cwd=self.workdir,
                                 capture_output=True, text=True).stdout
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.workdir,
                                capture_output=True, text=True).stdout
        self.assertEqual(commits, "")
        self.assertIn("?? a.txt", status)
        self.assertTrue(Checkpoints(self.workdir).list())

    def test_reads_and_dry_runs_take_no_snapshots(self):
        with FakeProvider(call("list_files"), call("write_file", path="a", content="x"),
                          text("done")):
            self.harness(policy=Policy("yolo", dry_run=True)).run("look")
        self.assertEqual(Checkpoints(self.workdir).list(), [])

    def test_cli_lists_and_undoes(self):
        with FakeProvider(call("write_file", path="a.txt", content="x"), text("done")):
            self.harness().run("edit")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["checkpoints", "-d", self.workdir]), 0)
            self.assertEqual(cli.main(["undo", "-d", self.workdir]), 0)
        self.assertIn("tool: write_file a.txt", out.getvalue())
        self.assertFalse(self.exists("a.txt"))


class TodoTests(Workdir):
    PLAN = "- [x] write tests\n- [ ] implement"

    def test_todo_is_normalised_shown_and_rejects_garbage(self):
        with FakeProvider(call("todo", items="[x] write tests\n* [ ] implement"),
                          call("todo", items="just do it"), text("ok")):
            harness = self.harness()
            harness.run("plan")
        self.assertEqual(harness.todo, self.PLAN)
        results = [m["text"] for m in harness.messages if m["role"] == "tool"]
        self.assertEqual(results[0], "Todo list updated: 1 of 2 done")
        self.assertTrue(results[1].startswith("ERROR: not a checklist line"))
        self.assertIn(("todo", {"items": self.PLAN}), self.events)

    def test_todo_survives_compaction_and_resume(self):
        script = [call("todo", items=self.PLAN), call("list_files"), call("list_files"),
                  call("list_files"), text("SUMMARY"), text("done")]
        with FakeProvider(*script) as fake:
            self.harness(budget_tokens=1, persist=True).run("long job")
        self.assertIn(f"Current todo list:\n{self.PLAN}", fake.requests[-1]["messages"][0]["text"])
        resumed = self.harness(persist=True)
        resumed.resume()
        self.assertEqual(resumed.todo, self.PLAN)


class HookTests(Workdir):
    def setUp(self):
        super().setUp()
        self.write("log_hook.py", "import sys\nopen('hook.log', 'a').write(sys.argv[1] + '\\n')\n")
        self.write("fail_hook.py", "print('generated file, do not edit'); raise SystemExit(3)\n")

    def test_after_hooks_run_on_matching_writes_only(self):
        hooks = [{"when": "after", "tool": "write_file", "match": "*.py",
                  "run": f"{PYTHON} log_hook.py {{path}}"}]
        with FakeProvider(call("write_file", path="src/app.py", content="x"),
                          call("write_file", path="notes.txt", content="x"),
                          call("read_file", path="src/app.py"), text("done")):
            self.harness(hooks=hooks).run("edit")
        self.assertEqual(self.read("hook.log"), "src/app.py\n")

    def test_failing_after_hook_output_reaches_the_model(self):
        hooks = [{"when": "after", "run": f"{PYTHON} fail_hook.py"}]
        with FakeProvider(call("write_file", path="a.txt", content="x"), text("done")):
            harness = self.harness(hooks=hooks)
            harness.run("edit")
        result = [m["text"] for m in harness.messages if m["role"] == "tool"][0]
        self.assertIn("failed (exit 3)", result)
        self.assertIn("generated file, do not edit", result)

    def test_failing_before_hook_blocks_the_call(self):
        hooks = [{"when": "before", "match": "gen/*", "run": f"{PYTHON} fail_hook.py"}]
        with FakeProvider(call("write_file", path="gen/api.py", content="x"), text("done")):
            harness = self.harness(hooks=hooks)
            harness.run("edit")
        result = [m["text"] for m in harness.messages if m["role"] == "tool"][0]
        self.assertTrue(result.startswith("BLOCKED: before-hook"))
        self.assertFalse(self.exists("gen/api.py"))

    def test_unsafe_paths_are_never_substituted_into_commands(self):
        hooks = [{"when": "after", "run": f"{PYTHON} log_hook.py {{path}}"}]
        with FakeProvider(call("write_file", path="a;echo pwned.txt", content="x"), text("done")):
            harness = self.harness(hooks=hooks)
            harness.run("edit")
        result = [m["text"] for m in harness.messages if m["role"] == "tool"][0]
        self.assertIn("skipped: unusual path", result)
        self.assertFalse(self.exists("hook.log"))

    def test_hooks_need_approval_in_safe_mode(self):
        asked = []
        policy = Policy("safe", approver=lambda c, r: asked.append(c["name"]) or c["name"] != "hook")
        hooks = [{"when": "after", "run": f"{PYTHON} log_hook.py x"}]
        with FakeProvider(call("write_file", path="a.txt", content="x"), text("done")):
            harness = self.harness(hooks=hooks, policy=policy)
            harness.run("edit")
        self.assertEqual(asked, ["write_file", "hook"])
        self.assertIn("not run: the user did not approve hook",
                      [m["text"] for m in harness.messages if m["role"] == "tool"][0])


if __name__ == "__main__":
    unittest.main()
