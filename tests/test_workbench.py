"""Tests for the workbench tools: jobs, terminals, code navigation, history, questions,
plan mode, presented files, goals, continuable sub-agents, workflows, and the harness
controls behind them (stop, steer, rewind, branch) and their terminal commands."""

import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.fake import FakeProvider, call, text
from zill import Harness, Policy, cli, goal, history, provider, tool
from zill.codenav import code_nav_tool
from zill.harness import Interrupted
from zill.jobs import Processes, process_tools
from zill.security import classify

WAIT = 15


def router(rules):
    """A thread-safe fake provider: replies follow the first rule key in the first user message."""
    lock, used = threading.Lock(), {}

    def complete(model, system, messages, tools, on_text=None):
        first = next(m["text"] for m in messages if m["role"] == "user")
        key = next(k for k in rules if k in first)
        with lock:
            step = used[key] = used.get(key, -1) + 1
        reply = rules[key][min(step, len(rules[key]) - 1)]
        return {**reply, "tool_calls": [dict(c, args=dict(c["args"])) for c in reply["tool_calls"]]}

    return mock.patch.object(provider, "complete", complete)


class Workdir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel, content):
        path = os.path.join(self.workdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def harness(self, **kwargs):
        kwargs.setdefault("checkpoints", False)
        harness = Harness(self.workdir, model="test-model", **kwargs)
        self.addCleanup(harness.close)
        return harness


class JobsAndTerminalsTests(Workdir):
    def setUp(self):
        super().setUp()
        self.processes = Processes(self.workdir)
        self.addCleanup(self.processes.close)
        self.tools = {t.name: t for t in process_tools(self.processes)}

    def wait_for(self, processes, job_id):
        deadline = time.monotonic() + WAIT
        while processes.jobs[job_id]["proc"].poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_a_background_job_reports_once_and_its_output_is_kept(self):
        self.assertIn("job_output 1", self.processes.start_job("echo hello from a job"))
        self.wait_for(self.processes, "1")
        self.assertEqual(len(self.processes.notices()), 1)
        self.assertEqual(self.processes.notices(), [])  # announced once
        output = self.tools["job_output"].run(job="1")
        self.assertIn("exited 0", output)
        self.assertIn("hello from a job", output)
        self.assertIn("echo hello", self.tools["job_list"].run())
        with self.assertRaisesRegex(ValueError, "no job 9"):
            self.tools["job_output"].run(job="9")

    def test_job_kill_stops_a_long_job(self):
        self.processes.start_job("ping -n 30 127.0.0.1" if os.name == "nt" else "sleep 30")
        self.assertEqual(self.processes.status(self.processes.jobs["1"]), "running")
        self.assertNotIn("running", self.tools["job_kill"].run(job="1"))

    def test_a_terminal_keeps_its_directory_and_reports_exit_codes(self):
        self.write("sub/marker.txt", "x")
        term = self.tools["terminal_open"].run().split()[-1]
        self.tools["terminal_send"].run(terminal=term, command="cd sub")
        listing = "dir /b" if os.name == "nt" else "ls"
        self.assertIn("marker.txt", self.tools["terminal_send"].run(terminal=term, command=listing))
        failing = "cmd /c exit 3" if os.name == "nt" else "(exit 3)"
        self.assertIn("[exit code: 3]",
                      self.tools["terminal_send"].run(terminal=term, command=failing))
        slow = "ping -n 3 127.0.0.1 >nul" if os.name == "nt" else "sleep 2"
        self.assertIn("still running",
                      self.tools["terminal_send"].run(terminal=term, command=slow, wait="0.2"))
        self.assertIn("Closed", self.tools["terminal_close"].run(terminal=term))

    def test_terminal_input_is_policed_like_bash(self):
        self.assertEqual(classify({"name": "terminal_send", "args": {"command": "sudo rm -rf /"}}),
                         "destructive")
        self.assertEqual(classify({"name": "terminal_send", "args": {"command": "curl x.io"}},
                                  self.tools["terminal_send"]), "network")

    def test_harness_bash_starts_background_jobs_and_notices_follow(self):
        with FakeProvider(call("bash", command="echo bg", background="true"),
                          call("list_files"), text("done")):
            harness = self.harness()
            original = harness.tools["list_files"].run

            def after_job(**kwargs):  # the notice needs the job to have finished
                self.wait_for(harness.processes, "1")
                return original(**kwargs)

            harness.tools["list_files"].run = after_job
            harness.run("start a job")
        results = [m["text"] for m in harness.messages if m["role"] == "tool"]
        self.assertIn("Started background job 1", results[0])
        self.assertIn("[ZILL: background job 1", results[1])


class CodeNavTests(Workdir):
    def setUp(self):
        super().setUp()
        self.write("shop/cart.py", 'class Cart:\n    def total(self, tax=0.2):\n'
                                   '        """Sum the items."""\n        return 1\n\n'
                                   'def greet(name):\n    return name\n')
        self.write("web/app.ts", "export function render(el) {}\nconst answer = render(1)\n")
        self.nav = code_nav_tool(self.workdir)

    def test_symbols_definitions_references_and_hover(self):
        symbols = self.nav.run(action="symbols", path="shop")
        self.assertIn("shop/cart.py:2: def Cart.total(self, tax=0.2)  # Sum the items.", symbols)
        self.assertIn("shop/cart.py:1: class Cart", symbols)
        self.assertIn("web/app.ts:1: function render  (heuristic)",
                      self.nav.run(action="definition", name="render"))
        self.assertIn("web/app.ts:2:", self.nav.run(action="references", name="render"))
        self.assertIn("return 1", self.nav.run(action="hover", name="Cart.total"))
        self.assertTrue(self.nav.run(action="fly", name="x").startswith("ERROR"))
        with self.assertRaises(PermissionError):
            self.nav.run(action="symbols", path="..")


class HistoryTests(Workdir):
    def test_search_rename_feedback_and_delete(self):
        with FakeProvider(text("the answer is 42")):
            harness = self.harness()
            harness.run("what is the answer")
        session_id = harness.audit_label()
        self.assertEqual(history.search(self.workdir, "ANSWER IS")[0]["role"], "assistant")
        self.assertIn(session_id, history.session_search_tool(self.workdir).run(query="42"))
        history.rename(self.workdir, session_id, "Life")
        self.assertEqual(history.session_rows(self.workdir)[0]["title"], "Life")
        history.feedback(self.workdir, session_id, 0, "good", "nice")
        with open(os.path.join(self.workdir, history.FEEDBACK_FILE), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["rating"], "good")
        with self.assertRaises(ValueError):
            history.feedback(self.workdir, session_id, 0, "meh")
        with self.assertRaises(ValueError):
            history.delete(self.workdir, "../escape")
        history.delete(self.workdir, session_id)
        self.assertEqual(history.session_rows(self.workdir), [])


class InteractionTests(Workdir):
    def test_questions_go_to_the_asker_and_headless_runs_proceed(self):
        asked = []

        def asker(request):
            asked.append(request)
            return "Blue"

        with FakeProvider(call("ask_user_question", question="Colour?",
                               options="Blue: calm\nGreen"), text("ok")):
            harness = self.harness(asker=asker)
            harness.run("pick")
        self.assertEqual(asked[0]["options"], [{"label": "Blue", "description": "calm"},
                                               {"label": "Green"}])
        self.assertEqual(harness.messages[2]["text"], "The user answered: Blue")
        with FakeProvider(call("ask_user_question", question="Colour?"), text("ok")):
            headless = self.harness()
            headless.run("pick")
        self.assertIn("did not answer", headless.messages[2]["text"])

    def test_plan_mode_blocks_changes_until_the_plan_is_approved(self):
        answers = [{"approved": False, "feedback": "add tests"}, {"approved": True}]
        script = [call("write_file", path="a.txt", content="x"),
                  call("exit_plan_mode", plan="1. write a"),
                  call("exit_plan_mode", plan="1. write a\n2. test"),
                  call("write_file", path="a.txt", content="x"), text("done")]
        with FakeProvider(*script) as fake:
            harness = self.harness(policy=Policy("yolo", plan=True),
                                   asker=lambda request: answers.pop(0))
            harness.run("build a")
        results = [m["text"] for m in harness.messages if m["role"] == "tool"]
        self.assertTrue(results[0].startswith("BLOCKED: plan mode"))
        self.assertIn("add tests", results[1])
        self.assertIn("approved", results[2])
        self.assertTrue(results[3].startswith("Wrote"))
        self.assertIn("Plan mode is on", fake.requests[0]["system"])
        self.assertFalse(harness.policy.plan)

    def test_present_accepts_only_real_files_inside(self):
        self.write("report.md", "# done")
        events = []
        with FakeProvider(call("present", paths="report.md, missing.md"),
                          call("present", paths="report.md", note="the report"), text("ok")):
            harness = self.harness(on_event=lambda kind, payload: events.append((kind, payload)))
            harness.run("hand over")
        results = [m["text"] for m in harness.messages if m["role"] == "tool"]
        self.assertTrue(results[0].startswith("ERROR"))
        self.assertEqual(harness.presented,
                         [{"path": "report.md", "bytes": 6, "note": "the report"}])
        self.assertIn(("present", {"files": harness.presented, "note": "the report"}), events)


class GoalTests(Workdir):
    def test_pursue_continues_until_the_model_completes_the_goal(self):
        script = [text("round one"), call("update_goal", status="complete", note="green"),
                  text("all done")]
        with FakeProvider(*script) as fake:
            harness = self.harness()
            self.assertEqual(harness.pursue("make tests pass"), "all done")
        self.assertEqual(harness.goal["status"], "complete")
        self.assertEqual(harness.goal["rounds"], 2)
        self.assertIn("Continue working toward the goal", fake.requests[1]["messages"][-1]["text"])
        self.assertEqual(goal.latest(harness.messages)["status"], "complete")

    def test_restating_the_goal_mid_pursuit_keeps_the_round_count(self):
        script = [call("create_goal", objective="restated"), text("a"), text("b"), text("c")]
        with FakeProvider(*script):
            harness = self.harness()
            harness.pursue("never ends", max_rounds=2)
        self.assertEqual((harness.goal["status"], harness.goal["rounds"]), ("paused", 2))

    def test_a_round_limit_pauses_the_goal(self):
        with FakeProvider(text("a"), text("b")):
            harness = self.harness()
            harness.pursue("never ends", max_rounds=2)
        self.assertEqual(harness.goal["status"], "paused")


class AgentTests(Workdir):
    def test_background_agents_report_through_notices_and_continue_on_send_message(self):
        rules = {"parent": [call("spawn_agent", task="child: count", background="true"),
                            call("list_agents"), text("parent done")],
                 "child": [text("counted 3"), text("counted again: 4")]}
        with router(rules):
            harness = self.harness()
            original = harness.tools["list_agents"].run

            def after_child(**kwargs):  # the notice needs the child to have finished
                harness.agents.agents["a1"]["thread"].join(WAIT)
                return original(**kwargs)

            harness.tools["list_agents"].run = after_child
            harness.run("parent task")
            results = [m["text"] for m in harness.messages if m["role"] == "tool"]
            self.assertIn("working in the background", results[0])
            # The notice rides on whichever result comes first after the child finished.
            self.assertEqual("".join(results).count("agent a1 (a1) done. Report:\ncounted 3"), 1)
            reply = harness.tools["send_message"].run(agent="a1", message="again")
        self.assertEqual(reply, "counted again: 4")
        self.assertEqual(len(harness.agents.agents["a1"]["harness"].messages), 4)

    def test_a_workflow_runs_dependencies_first_and_passes_reports_on(self):
        steps = [{"name": "api", "task": "api step"}, {"name": "docs", "task": "docs step"},
                 {"name": "tests", "task": "tests step", "after": ["api", "docs"]}]
        seen = {}
        replies = {"api step": "API ready", "docs step": "docs ready", "tests step": "tests pass"}

        def complete(model, system, messages, tools, on_text=None):
            key = next(k for k in replies if messages[0]["text"].startswith(k))
            seen[key] = messages[0]["text"]
            return {"text": replies[key], "tool_calls": [], "usage": {}}

        with mock.patch.object(provider, "complete", complete):
            harness = self.harness()
            report = harness.tools["workflow"].run(steps=json.dumps(steps))
            cycle = [{"name": "a", "task": "x", "after": ["b"]},
                     {"name": "b", "task": "y", "after": ["a"]}]
            self.assertIn("cycle", harness.tools["workflow"].run(steps=json.dumps(cycle)))
        self.assertIn("## tests\ntests pass", report)
        self.assertIn("Report from step api:\nAPI ready", seen["tests step"])
        self.assertIn("Report from step docs:\ndocs ready", seen["tests step"])
        self.assertTrue(harness.tools["workflow"].run(steps="[1]").startswith("ERROR"))


class ControlTests(Workdir):
    def test_stop_from_another_thread_ends_the_run_and_the_log_repairs(self):
        started = threading.Event()

        @tool("A slow read.", risk="read")
        def slow():
            started.set()
            time.sleep(0.5)
            return "slow result"

        two_calls = {"text": "", "usage": {}, "tool_calls": [{"name": "slow", "args": {}},
                                                             {"name": "slow", "args": {}}]}
        with FakeProvider(two_calls, text("never")):
            harness = self.harness(extra_tools=[slow])
            threading.Thread(target=lambda: started.wait(WAIT) and harness.stop()).start()
            with self.assertRaises(Interrupted):
                harness.run("go")
            harness.resume(harness.session_path)
        results = [m["text"] for m in harness.messages if m["role"] == "tool"]
        self.assertEqual(results[0], "slow result")
        self.assertIn("Interrupted", results[1])

    def test_steered_messages_join_before_the_next_model_call(self):
        with FakeProvider(call("list_files"), text("done")) as fake:
            harness = self.harness()
            original = harness.tools["list_files"].run

            def list_and_steer(**kwargs):
                harness.steer("use tabs")
                return original(**kwargs)

            harness.tools["list_files"].run = list_and_steer
            harness.run("format")
        steered = fake.requests[1]["messages"][-1]
        self.assertEqual(steered["auto"], "steer")
        self.assertIn("use tabs", steered["text"])
        self.assertEqual(len(harness.turn_starts(harness.transcript())), 1)

    def test_rewind_and_branch_cut_at_turn_boundaries_and_record_usage(self):
        reply = {"text": "one", "tool_calls": [], "usage": {"input": 7, "output": 2}}
        with FakeProvider(reply, text("two"), text("three")):
            harness = self.harness()
            harness.run("first")
            harness.run("second")
            self.assertEqual(harness.messages[1]["usage"], {"input": 7, "output": 2})
            original = harness.session_path
            self.assertEqual(harness.rewind(1), "second")
            self.assertEqual([m["text"] for m in harness.transcript()], ["first", "one"])
            harness.run("second again")
            branched = harness.branch(0)
        self.assertNotEqual(branched, original)
        self.assertEqual([m["text"] for m in harness.transcript()], ["first", "one"])
        self.assertEqual(len(history.session_rows(self.workdir)), 2)
        with self.assertRaises(ValueError):
            harness.rewind(5)


class SecretTests(Workdir):
    def test_tool_results_are_redacted_before_the_model_sees_them(self):
        self.write("config.txt", "token=sk-workbench-secret-99231\n")
        with mock.patch.dict(os.environ, {"SERVICE_API_KEY": "sk-workbench-secret-99231"}), \
                FakeProvider(call("read_file", path="config.txt"), text("done")) as fake:
            self.harness().run("show me the config")
        seen = json.dumps(fake.requests[1]["messages"])
        self.assertNotIn("sk-workbench-secret-99231", seen)
        self.assertIn("[REDACTED]", seen)


class TerminalCommandTests(Workdir):
    def test_ask_user_reads_numbers_or_free_text_and_reviews_plans(self):
        question = {"kind": "question", "question": "Colour?", "header": "", "multi_select": True,
                    "options": [{"label": "Blue"}, {"label": "Green"}]}
        with redirect_stdout(io.StringIO()):
            with mock.patch("builtins.input", return_value="1, 2"):
                self.assertEqual(cli.ask_user(question), "Blue, Green")
            with mock.patch("builtins.input", return_value="teal"):
                self.assertEqual(cli.ask_user(question), "teal")
            with mock.patch("builtins.input", return_value="y"):
                self.assertEqual(cli.ask_user({"kind": "plan", "plan": "1."}),
                                 {"approved": True, "feedback": ""})
            with mock.patch("builtins.input", return_value="smaller steps"):
                self.assertEqual(cli.ask_user({"kind": "plan", "plan": "1."}),
                                 {"approved": False, "feedback": "smaller steps"})

    def test_slash_commands_for_plans_goals_turns_and_history(self):
        script = [text("hi"), text("hi again"), call("update_goal", status="complete"),
                  text("goal met")]
        with FakeProvider(*script), redirect_stdout(io.StringIO()) as out:
            harness = self.harness()
            cli._slash(harness, "/plan on")
            self.assertTrue(harness.policy.plan)
            cli._slash(harness, "/plan off")
            cli._slash(harness, "/retry")
            harness.run("hello")
            cli._slash(harness, "/retry")
            self.assertEqual([m["text"] for m in harness.transcript()], ["hello", "hi again"])
            cli._slash(harness, "/goal ship it")
            self.assertEqual(harness.goal["status"], "complete")
            cli._slash(harness, "/feedback good great")
            cli._slash(harness, "/search hello")
            cli._slash(harness, "/jobs")
            cli._slash(harness, "/branch")
        printed = out.getvalue()
        self.assertIn("plan mode: on", printed)
        self.assertIn("nothing to work with yet", printed)
        self.assertIn("Goal (complete", printed)
        self.assertIn("no jobs, terminals or sub-agents", printed)
        self.assertIn("continuing in a new session", printed)
        self.assertTrue(os.path.exists(os.path.join(self.workdir, history.FEEDBACK_FILE)))


if __name__ == "__main__":
    unittest.main()
