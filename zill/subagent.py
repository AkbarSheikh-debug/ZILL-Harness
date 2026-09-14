"""Day 4 — Sub-agents: hand self-contained tasks to fresh conversations, and keep them.

Concept: a long task pollutes one context with every file read and every
failed attempt. spawn_agent starts a child harness with an empty transcript,
gives it only the task text, and returns only its final report. The parent
pays for one tool result, not for the child's whole journey. A child is kept:
send_message continues its conversation, list_agents shows every child, and
interrupt_agent stops one. workflow runs several children as a small graph.

Design rules:
  * The child sees nothing of the parent's conversation; the task string must
    carry everything it needs.
  * Recursion is bounded: at max_depth the tools refuse and tell the model to
    do the work itself, so delegation can never loop forever.
  * The factory is injected. subagent.py knows nothing about how a harness is
    built, which keeps it free of a circular import with harness.py.
  * Delegating is read-risk: children run under the parent's Policy, so each
    of their tool calls is gated on its own and approving twice adds nothing.
  * background "true" runs a child on a thread. Its report reaches the parent
    as a notice on the parent's next tool result, and list_agents shows it.
  * A workflow runs in waves: every step whose dependencies have reported
    runs at once, and each step's task is followed by the reports it depends on.
"""

import itertools
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import session
from .tools import tool

DEPTH_LIMIT = "ERROR: sub-agent depth limit reached; do this task yourself"
MAX_STEPS = 12
REPORT_CLIP = 6_000
BAD_STEPS = (f"ERROR: steps must be a JSON list of 1 to {MAX_STEPS} objects with a string "
             f"name and task, and an optional after list of step names")


def _valid(steps):
    """True if steps is a well-formed workflow list."""
    return (isinstance(steps, list) and 0 < len(steps) <= MAX_STEPS
            and all(isinstance(s, dict) and isinstance(s.get("name"), str)
                    and isinstance(s.get("task"), str) and isinstance(s.get("after", []), list)
                    for s in steps))


class Agents:
    """The children one harness has started."""

    def __init__(self, make_harness, depth=0, max_depth=2):
        self.make, self.depth, self.max_depth = make_harness, depth, max_depth
        self.agents, self._announced = {}, set()
        self._ids = itertools.count(1)

    def new(self, name=""):
        """Build a child and register it; return its record."""
        agent_id = f"a{next(self._ids)}"
        agent = {"id": agent_id, "name": name or agent_id, "harness": self.make(self.depth + 1),
                 "status": "idle", "report": "", "thread": None}
        self.agents[agent_id] = agent
        return agent

    def run(self, agent, message):
        """Run one message in agent's conversation; return and record its report."""
        agent["status"], child = "running", agent["harness"]
        self._announced.discard(agent["id"])
        try:
            agent["report"], agent["status"] = child.run(message), "done"
        except KeyboardInterrupt:
            child.messages = session.repair(child.messages)
            agent["report"], agent["status"] = "(interrupted before it finished)", "interrupted"
        except Exception as err:  # the parent reads the failure; it does not crash
            agent["report"], agent["status"] = f"ERROR: {type(err).__name__}: {err}", "failed"
        return agent["report"]

    def start(self, agent, message, background):
        """Run message now and return the report, or on a thread and return a receipt."""
        if not background:
            return self.run(agent, message)
        agent["status"] = "running"
        agent["thread"] = threading.Thread(target=self.run, args=(agent, message), daemon=True)
        agent["thread"].start()
        return (f"Agent {agent['id']} ({agent['name']}) is working in the background. Its "
                f"report arrives as a notice; list_agents shows its status.")

    def get(self, agent_id):
        """Return the agent named agent_id, or raise ValueError."""
        agent = self.agents.get(str(agent_id).strip())
        if agent is None:
            raise ValueError(f"no agent {agent_id}; list_agents shows them")
        return agent

    def rows(self):
        """Describe every child, for list_agents and the app."""
        return [{"id": a["id"], "name": a["name"], "status": a["status"],
                 "messages": len(a["harness"].messages), "usage": dict(a["harness"].usage),
                 "report": a["report"][:REPORT_CLIP]} for a in self.agents.values()]

    def notices(self):
        """Return a notice for each background child that finished since the last one."""
        lines = []
        for agent in self.agents.values():
            if (agent["thread"] and agent["status"] != "running"
                    and agent["id"] not in self._announced):
                self._announced.add(agent["id"])
                lines.append(f"[ZILL: agent {agent['id']} ({agent['name']}) {agent['status']}. "
                             f"Report:\n{agent['report'][:REPORT_CLIP]}]")
        return lines

    def close(self):
        """Stop every child and the processes it started."""
        for agent in self.agents.values():
            agent["harness"].stop()
            agent["harness"].close()

    def workflow(self, steps, max_parallel):
        """Run steps as waves of parallel children; return every step's report."""
        names = [s["name"] for s in steps]
        if len(set(names)) != len(names):
            return "ERROR: step names must be unique"
        unknown = {d for s in steps for d in s.get("after", [])} - set(names)
        if unknown:
            return f"ERROR: steps depend on unknown steps: {', '.join(sorted(map(str, unknown)))}"
        reports, remaining = {}, list(steps)

        def run_step(step):
            inputs = "".join(f"\n\nReport from step {d}:\n{reports[d][:REPORT_CLIP]}"
                             for d in step.get("after", []))
            return self.run(self.new(step["name"]), step["task"] + inputs)

        while remaining:
            ready = [s for s in remaining if all(d in reports for d in s.get("after", []))]
            if not ready:
                return "ERROR: the steps' dependencies form a cycle"
            with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(ready)))) as pool:
                for step, report in zip(ready, pool.map(run_step, ready)):
                    reports[step["name"]] = report
            remaining = [s for s in remaining if s["name"] not in reports]
        return "\n\n".join(f"## {name}\n{reports[name][:REPORT_CLIP]}" for name in names)

    def tools(self):
        """Return spawn_agent, send_message, list_agents, interrupt_agent and workflow."""
        limited = self.depth >= self.max_depth

        @tool("Delegate a self-contained task to a fresh sub-agent with its own clean "
              "context. The child cannot see this conversation, so the task must "
              "include every path, requirement and detail it needs. Returns the "
              "child's final report; list_agents gives its id for send_message.",
              risk="read", task="Complete, standalone instructions for the sub-agent",
              name="A short name for the agent (optional)",
              background="'true' to return at once and get the report later as a notice")
        def spawn_agent(task, name="", background="false"):
            if limited:
                return DEPTH_LIMIT
            return self.start(self.new(name), task, str(background).lower() == "true")

        @tool("Send a follow-up message to a sub-agent. An idle agent continues its own "
              "conversation and replies; a running one reads it at its next step.",
              risk="read", agent="Agent id from spawn_agent or list_agents",
              message="What to tell or ask the agent",
              background="'true' to return at once and get the reply later as a notice")
        def send_message(agent, message, background="false"):
            entry = self.get(agent)
            if entry["status"] == "running":
                entry["harness"].steer(message)
                return f"Delivered to running agent {entry['id']}; it reads it at its next step."
            return self.start(entry, message, str(background).lower() == "true")

        @tool("List sub-agents with their status and latest report.", risk="read")
        def list_agents():
            return "\n\n".join(f"{r['id']} {r['name']} [{r['status']}, {r['messages']} "
                               f"messages]\n{r['report'][:500]}" for r in self.rows()) or "(no agents)"

        @tool("Stop a running sub-agent at its next step.", risk="read",
              agent="Agent id from list_agents")
        def interrupt_agent(agent):
            entry = self.get(agent)
            entry["harness"].stop()
            return f"Asked agent {entry['id']} to stop."

        @tool("Run a workflow of sub-agent tasks. Steps with no unfinished dependencies run "
              "in parallel; a step starts once the steps it lists in after have reported, and "
              "its task is followed by their reports. Returns every step's report.",
              risk="read",
              steps='JSON list, e.g. [{"name": "api", "task": "..."}, '
                    '{"name": "tests", "task": "...", "after": ["api"]}]',
              max_parallel="Most steps running at once (default 3)")
        def workflow(steps, max_parallel="3"):
            if limited:
                return DEPTH_LIMIT
            try:
                parsed = json.loads(steps)
            except ValueError:
                return BAD_STEPS
            return self.workflow(parsed, int(max_parallel)) if _valid(parsed) else BAD_STEPS

        return [spawn_agent, send_message, list_agents, interrupt_agent, workflow]


def subagent_tool(make_harness, depth=0, max_depth=2):
    """Return just the spawn_agent tool; make_harness(depth) builds each child."""
    return Agents(make_harness, depth, max_depth).tools()[0]
