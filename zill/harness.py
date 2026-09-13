"""Day 4 — The harness: one object that composes every layer.

Concept: the other modules build the parts: a provider, a loop, tools, a
policy, a context engine, memory, skills, checkpoints, hooks. Harness wires
them into one agent bound to one working directory, records every message
to a session file as it lands, and can pick a crashed conversation back up
where the file ends.

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
    become the "latest" session that --resume picks up. They share the
    parent's Policy object, so dry-run and approvals bind them too, and they
    write to the parent's audit log under the parent's session name.
  * Every tool call is decided by Policy.decide() with the Tool in hand (its
    risk and source), and recorded in the audit log when it finishes.
  * Before an allowed state-changing call runs: before-hooks, then a
    checkpoint. After it runs: after-hooks. Reads trigger neither.
  * A run is done when the model stops and the verify command passes; a
    failing check goes back to the model for up to MAX_FIX_ROUNDS rounds.
    Hook and verify commands are decided by Policy like bash calls.
"""

import os
import time
import uuid

from . import (__version__, audit, config, context, credentials, hooks, loop, memory,
               provider, session, skills, todo, web)
from .checkpoints import TOOL_PREFIX, Checkpoints
from .security import Decision, Policy
from .subagent import subagent_tool
from .tools import Tool, core_tools, tool

COMPACT_AT = 0.6  # compact once the transcript fills this share of the window
MAX_FIX_ROUNDS = 3
VERIFY_FAILED = ("Verification failed: `{command}` exited {code}.\n{output}\n\n"
                 "Find and fix the cause, then finish with a short summary.")


def _ignore(kind, payload):
    """Default on_event: show nothing."""


class Harness:
    """A persistent, policy-gated agent working inside one directory."""

    def __init__(self, workdir=".", model=None, policy=None, extra_tools=None,
                 system_extra="", on_event=None, budget_tokens=None, max_turns=120,
                 session_path=None, enable_subagents=True, persist=True, verify=None,
                 hooks=None, checkpoints=True, allowed_tools=None, stream=False,
                 extensions=True, _depth=0, _audit_label=None):
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
        self._audit_label = _audit_label
        self._pending = {}  # call id -> (call, tool, decision) awaiting tool_end
        # None means "from .zill/project.json"; "" or [] switch the feature off.
        project = (config.load_project(self.workdir) if verify is None or hooks is None
                   else {})
        self.verify = project.get("verify") if verify is None else (verify or None)
        self.hooks = project.get("hooks", []) if hooks is None else hooks
        self.checkpoints = Checkpoints(self.workdir) if checkpoints else None
        self.todo = ""
        self.stream = stream
        self.usage = {"calls": 0, "input": 0, "output": 0}  # model calls this harness made

        self.tools = {t.name: t for t in core_tools(self.workdir) + web.web_tools()}
        self.notes, self._mcp_clients = [], []  # extension problems, and servers to close

        @tool("Save a durable fact about this project to memory for future sessions.",
              risk="write", note="One self-contained fact worth keeping")
        def remember(note):
            return memory.remember(self.workdir, note)

        def update_todo(checklist):
            self.todo = checklist
            self.on_event("todo", {"items": checklist})

        for extra in (remember, todo.todo_tool(update_todo)):
            self.tools[extra.name] = extra
        if skills.catalog(self.workdir):
            self.tools.update({t.name: t for t in skills.skill_tools(self.workdir)})
        # MCP servers and plugins load once, at the top; children reuse the same tools.
        loaded = self._load_extensions() if extensions is True else list(extensions or [])
        if enable_subagents:
            def make_child(depth):
                """Build an ephemeral child over the same directory, policy, hooks and tools."""
                return Harness(self.workdir, model=self.model, policy=self.policy,
                               on_event=self.on_event, budget_tokens=self.budget_tokens,
                               max_turns=max_turns, persist=False, verify="",
                               hooks=self.hooks, checkpoints=checkpoints,
                               allowed_tools=allowed_tools, extensions=loaded, _depth=depth,
                               _audit_label=f"{self.audit_label()} (sub-agent)")

            spawn = subagent_tool(make_child, depth=_depth)
            self.tools[spawn.name] = spawn
        if allowed_tools is not None:  # a profile narrows the built-in tools
            self.tools = {n: t for n, t in self.tools.items() if n in allowed_tools}
        for extension in loaded:  # namespaced names, but never let one replace a builtin
            if extension.name in self.tools:
                self.notes.append(f"tool name collision: {extension.name} was not loaded")
            else:
                self.tools[extension.name] = extension
        self.tools.update({t.name: t for t in extra_tools or []})
        extra = "\n\n".join(part for part in (skills.catalog_prompt(self.workdir), system_extra)
                            if part)
        self.system = memory.build_system_prompt(self.workdir, extra)

    def resume(self, path=None):
        """Load a session (the latest by default); return True if it had messages."""
        path = path or session.latest(self.workdir)
        if path is None:
            return False
        self.messages = session.load(path)
        self.session_path = path
        self.todo = todo.latest(self.messages)
        if self.persist:
            self._rewrite()
        self._recorded = len(self.messages)
        return bool(self.messages)

    def run(self, task):
        """Add task to the conversation, drive the loop until verified, return the final text."""
        if self.persist and self.session_path is None:
            self.session_path = session.new_session(self.workdir, task[:32])
            session.write_meta(self.session_path, self.metadata())
        self.messages.append({"role": "user", "text": task})
        self._flush()

        def on_event(kind, payload):
            self._flush()
            if kind == "provider_end":
                self.usage["calls"] += 1
                for field in ("input", "output"):
                    self.usage[field] += payload["usage"].get(field, 0)
            if kind == "tool_end":
                call, tool_obj, decision = self._pending.pop(
                    payload["id"], ({"name": payload["name"], "args": {}}, None, None))
                audit.record(self.workdir, self.audit_label(), call, tool_obj, decision,
                             payload["result"], payload["seconds"])
            self.on_event(kind, payload)

        def before_tool(call):
            self._flush()
            tool_obj = self.tools.get(call["name"])
            decision = self.policy.decide(call, tool_obj)
            if decision.allowed and decision.risk != "read":
                refusal = self._before_hooks(call)
                if refusal:
                    decision = Decision(False, refusal, decision.risk, decision.needs_approval)
                else:
                    self._checkpoint(call)
            self._pending[call["id"]] = (call, tool_obj, decision)
            return None if decision.allowed else decision.reason

        def after_tool(call, result):
            _, _, decision = self._pending.get(call["id"], (None, None, None))
            if decision is None or decision.risk == "read":
                return result
            return self._after_hooks(call, result)

        def before_turn(messages):
            self._flush()
            return self._compacted(messages, self.budget_tokens)

        def drive():
            return loop.run_loop(self.model, self.system, self.messages, self.tools,
                                 on_event, before_tool, max_turns=self.max_turns,
                                 before_turn=before_turn, after_tool=after_tool,
                                 stream=self.stream)

        self.on_event("session_start", {"session": self.session_path, "model": self.model})
        try:
            text = drive()
            for fix_round in range(MAX_FIX_ROUNDS + 1):
                if not self.verify:
                    break
                code, output = self._run_config_command("verify", self.verify)
                self.on_event("verify", {"command": self.verify, "exit": code,
                                         "round": fix_round})
                if code is None:
                    text = f"{text}\n\n(Verification not run: {output})"
                    break
                if code == 0:
                    break
                if fix_round == MAX_FIX_ROUNDS:
                    text = (f"{text}\n\nVerification still failing after {MAX_FIX_ROUNDS} "
                            f"fix rounds: `{self.verify}` exited {code}.")
                    break
                self.messages.append({"role": "user", "text": VERIFY_FAILED.format(
                    command=self.verify, code=code, output=output or "(no output)")})
                text = drive()
            return text
        except Exception as err:
            self.on_event("error", {"type": type(err).__name__,
                                    "message": credentials.redact(str(err))})
            raise
        finally:
            self._flush()
            self.on_event("session_end", {"session": self.session_path})

    def _load_extensions(self):
        """Start trusted MCP servers and load enabled plugins; return their tools."""
        from . import mcp, plugins  # edge packages load only when a harness needs them
        mcp_tools, self._mcp_clients, mcp_notes = mcp.load_tools(self.workdir)
        plugin_tools, plugin_notes = plugins.load_tools(self.workdir)
        self.notes += mcp_notes + plugin_notes
        return mcp_tools + plugin_tools

    def close(self):
        """Stop the MCP servers this harness started."""
        for client in self._mcp_clients:
            client.close()

    def clear(self):
        """Start a fresh conversation; the next run opens a new session file."""
        self.messages, self._recorded, self.session_path, self.todo = [], 0, None, ""

    def compact(self):
        """Summarise older turns now, whatever the budget; return (before, after) counts."""
        before = len(self.messages)
        self.messages = self._compacted(self.messages, budget_tokens=0)
        return before, len(self.messages)

    def _compacted(self, messages, budget_tokens):
        """Compact messages against budget, keeping the todo list and the log consistent."""
        compacted = context.compact(self.model, messages, budget_tokens)
        if compacted is not messages:
            if self.todo:  # the plan must outlive the details compaction drops
                compacted[0] = {**compacted[0], "text": f"{compacted[0]['text']}\n\n"
                                                        f"Current todo list:\n{self.todo}"}
            self.on_event("compaction", {"before": len(messages), "after": len(compacted)})
        # Compaction shrinks the list; the log keeps the full history.
        self._recorded = min(self._recorded, len(compacted))
        return compacted

    def audit_label(self):
        """Name this harness's entries in the audit log: its session, or its parent's."""
        if self._audit_label:
            return self._audit_label
        if self.session_path:
            return os.path.splitext(os.path.basename(self.session_path))[0]
        return "ephemeral"

    def metadata(self):
        """Describe this harness for the session record; never includes secrets."""
        return {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "zill_version": __version__, "model": self.model,
                "model_info": provider.model_info(self.model), "workdir": self.workdir,
                "mode": self.policy.mode, "dry_run": self.policy.dry_run,
                "tools": {name: {"source": t.source, "risk": t.risk}
                          for name, t in sorted(self.tools.items())},
                "skills": sorted(skills.catalog(self.workdir)),
                "verify": self.verify, "hooks": len(self.hooks),
                "checkpoints": bool(self.checkpoints and self.checkpoints.available)}

    def _run_config_command(self, kind, command):
        """Run a hook or verify command through Policy; return (exit, output) or (None, reason)."""
        call = {"id": f"{kind}_{uuid.uuid4().hex[:8]}", "name": kind, "args": {"command": command}}
        tool_obj = Tool(name=kind, spec={}, run=None, source="config", risk="execute")
        decision = self.policy.decide(call, tool_obj)
        if not decision.allowed:
            audit.record(self.workdir, self.audit_label(), call, tool_obj, decision,
                         f"BLOCKED: {decision.reason}", 0.0)
            return None, decision.reason
        started = time.monotonic()
        code, output = hooks.run_command(command, self.workdir)
        audit.record(self.workdir, self.audit_label(), call, tool_obj, decision,
                     "ok" if code == 0 else f"ERROR: exit {code}",
                     round(time.monotonic() - started, 3))
        return code, output

    def _before_hooks(self, call):
        """Run matching before-hooks; return a refusal reason, or None to proceed."""
        for hook in hooks.matching(self.hooks, "before", call):
            command = hooks.command_for(hook, call)
            if command is None:
                return f"before-hook {hook['run']!r} cannot run on an unusual path; rename it"
            code, output = self._run_config_command("hook", command)
            if code is None:
                return f"before-hook `{command}` was not allowed: {output}"
            if code != 0:
                return f"before-hook `{command}` failed (exit {code}): {output}"
        return None

    def _after_hooks(self, call, result):
        """Run matching after-hooks; return result with any failures appended."""
        notes = []
        for hook in hooks.matching(self.hooks, "after", call):
            command = hooks.command_for(hook, call)
            if command is None:
                notes.append(f"[after-hook {hook['run']!r} skipped: unusual path]")
                continue
            code, output = self._run_config_command("hook", command)
            if code is None:
                notes.append(f"[after-hook `{command}` not run: {output}]")
            elif code != 0:
                notes.append(f"[after-hook `{command}` failed (exit {code})]\n{output}")
        return "\n".join([result, *notes])

    def _checkpoint(self, call):
        """Snapshot the work tree before a state-changing call; never block the call."""
        if not (self.checkpoints and self.checkpoints.available):
            return
        args = call["args"]
        detail = args.get("path") or str(args.get("command", ""))[:60]
        label = f"{TOOL_PREFIX}{call['name']} {detail}".strip()
        try:
            ref = self.checkpoints.snapshot(label)
        except RuntimeError as err:
            self.on_event("checkpoint", {"id": None, "label": label,
                                         "error": credentials.redact(str(err))})
            return
        self.on_event("checkpoint", {"id": ref, "label": label})

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
