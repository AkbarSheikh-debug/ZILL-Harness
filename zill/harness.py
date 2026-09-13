"""Day 4 — The harness: one object that composes the whole week.

Concept: days 1–3 built the parts — a provider, a loop, tools, a policy, a
context engine, memory, skills. Harness wires them into one agent bound to
one working directory, records every message to a session file as it lands,
and can pick a crashed conversation back up where the file ends.

Design rules:
  * Composition only. Each capability still lives in its own module; this
    file decides what is plugged into which socket and nothing more.
  * A message is on disk before the next thing happens: the transcript is
    flushed before every model call and every tool execution, so a kill
    loses at most the step in flight, which session.load() then repairs.
  * resume() rewrites the session file once, atomically, from the repaired
    transcript. That drops a torn tail and records the interruption notices,
    so later appends are never hidden behind a line load() stops at.
  * Children are ephemeral (persist=False): a sub-agent's log must never
    become the "latest" session that --resume picks up.
"""

import os
import time

from . import __version__, context, loop, memory, provider, session, skills
from .security import Policy
from .subagent import subagent_tool
from .tools import core_tools, tool


COMPACT_AT = 0.6  # compact once the transcript fills this share of the window


def _ignore(kind, payload):
    """Default on_event: show nothing."""


class Harness:
    """A persistent, policy-gated agent working inside one directory."""

    def __init__(self, workdir=".", model=None, policy=None, extra_tools=None,
                 system_extra="", on_event=None, budget_tokens=None, max_turns=120,
                 session_path=None, enable_subagents=True, persist=True, _depth=0):
        self.workdir = os.path.realpath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.model = model or provider.default_model()
        self.policy = policy or Policy("yolo")
        self.on_event = on_event or _ignore
        window = provider.model_info(self.model).get("context_window", 1_000_000)
        self.budget_tokens = budget_tokens or int(window * COMPACT_AT)
        self.max_turns = max_turns
        self.session_path = session_path
        self.persist = persist
        self.messages = []
        self._recorded = 0  # messages[:_recorded] are already on disk

        self.tools = {t.name: t for t in core_tools(self.workdir)}

        @tool("Save a durable fact about this project to memory for future sessions.",
              note="One self-contained fact worth keeping")
        def remember(note):
            return memory.remember(self.workdir, note)

        self.tools[remember.name] = remember
        if skills.catalog(self.workdir):
            self.tools.update({t.name: t for t in skills.skill_tools(self.workdir)})
        if enable_subagents:
            def make_child(depth):
                """Build an ephemeral child over the same directory and policy."""
                return Harness(self.workdir, model=self.model, policy=self.policy,
                               on_event=self.on_event, budget_tokens=self.budget_tokens,
                               max_turns=max_turns, persist=False, _depth=depth)

            spawn = subagent_tool(make_child, depth=_depth)
            self.tools[spawn.name] = spawn
        self.tools.update({t.name: t for t in extra_tools or []})
        self.system = memory.build_system_prompt(
            self.workdir, skills.catalog_prompt(self.workdir) + system_extra)

    def resume(self, path=None):
        """Load a session (the latest by default); return True if it had messages."""
        path = path or session.latest(self.workdir)
        if path is None:
            return False
        self.messages = session.load(path)
        self.session_path = path
        if self.persist:
            self._rewrite()
        self._recorded = len(self.messages)
        return bool(self.messages)

    def run(self, task):
        """Add task to the conversation, drive the loop, and return the final text."""
        if self.persist and self.session_path is None:
            self.session_path = session.new_session(self.workdir, task[:32])
            session.write_meta(self.session_path, self.metadata())
        self.messages.append({"role": "user", "text": task})
        self._flush()

        def on_event(kind, payload):
            self._flush()
            self.on_event(kind, payload)

        def before_tool(call):
            self._flush()
            return self.policy.check(call)

        def before_turn(messages):
            self._flush()
            compacted = context.compact(self.model, messages, self.budget_tokens)
            # Compaction shrinks the list; the log keeps the full history.
            self._recorded = min(self._recorded, len(compacted))
            return compacted

        try:
            return loop.run_loop(self.model, self.system, self.messages, self.tools,
                                 on_event, before_tool, max_turns=self.max_turns,
                                 before_turn=before_turn)
        finally:
            self._flush()

    def metadata(self):
        """Describe this harness for the session record; never includes secrets."""
        return {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "zill_version": __version__, "model": self.model,
                "model_info": provider.model_info(self.model), "workdir": self.workdir,
                "mode": self.policy.mode, "tools": sorted(self.tools),
                "skills": sorted(skills.catalog(self.workdir))}

    def _flush(self):
        """Append every message not yet recorded to the session file."""
        self._recorded = min(self._recorded, len(self.messages))
        if self.persist and self.session_path:
            for message in self.messages[self._recorded:]:
                session.append(self.session_path, message)
        self._recorded = len(self.messages)

    def _rewrite(self):
        """Replace the session file with the current transcript, atomically."""
        temp = f"{self.session_path}.tmp"
        if os.path.exists(temp):
            os.remove(temp)
        for message in self.messages:
            session.append(temp, message)
        os.replace(temp, self.session_path)
