"""Day 5 — The command line: the front door to the harness.

Concept: `zill` opens a prompt loop over one persistent session; `zill run
"task"` (or -p) runs one task headlessly and exits; subcommands in
commands.py check, inspect and manage everything else. Every path resolves
its settings through settings.resolve and builds the same Harness, so the
terminal adds only what a person needs to see and approve.

Design rules:
  * Presentation only. The CLI prints events and answers approvals; tools,
    policy, persistence and compaction stay in their own modules.
  * The default mode follows who is watching: safe when a person can answer
    approval prompts, yolo when a task runs headlessly. A project file may
    only make it stricter.
  * Ctrl-C stops the run, never the session. The log is flushed as it grows,
    so the interrupted transcript is reloaded (and its cut-off calls repaired)
    before the next prompt, and --resume picks it up in a later process.
  * Output is bounded: one line per tool call, one dimmed line per result.
  * Nothing reaches the terminal unredacted. Streamed text is printed a line
    at a time, so a key split across fragments is still masked.
  * --json prints exactly one JSON object and nothing else, for scripts.
  * An interactive start with no usable key runs `zill setup` first, so
    installing and pasting a key is enough.
"""

import argparse
import json
import sys
import threading

from . import commands, config, cost, credentials, goal, history, provider, settings, skills
from .jobs import _kill
from .profiles import PROFILES
from .security import MODES

ARGS_CLIP = 100  # characters of a tool call's JSON arguments shown
RESULT_CLIP = 120  # characters of a result's first line shown
BAR = 30  # characters in the /context fill bar
DIM, RESET = "\033[2m", "\033[0m"
PROMPT = "zill> "
SLASH = {
    "/help": "show these commands",
    "/context": "how full the context window is, by part",
    "/cost": "tokens used and estimated cost",
    "/model": "pick a model from a list, or switch: /model anthropic:claude-opus-5",
    "/mode": "show the mode, or switch: /mode safe | yolo | read-only",
    "/compact": "summarise older turns now to free context",
    "/clear": "start a fresh conversation (new session)",
    "/plan": "plan mode: /plan on | off (reads only until you approve a plan)",
    "/goal": "keep working toward a goal over turns: /goal OBJECTIVE | /goal | /goal clear",
    "/retry": "run your last message again",
    "/branch": "continue in a new session copied from this one",
    "/jobs": "background jobs, terminals and sub-agents",
    "/kill": "stop a job, terminal or sub-agent: /kill ID",
    "/search": "search earlier sessions here: /search TEXT",
    "/feedback": "rate the last reply: /feedback good | bad [note]",
    "/todo": "show the agent's checklist",
    "/undo": "undo the agent's latest change",
    "/checkpoints": "list checkpoints",
    "/sessions": "list saved sessions here",
    "/skills": "list the skills in ./skills",
    "/exit": "quit (Ctrl-D works too)",
}
_streamed = []  # text fragments not yet printed: flushed a whole line at a time


def _clip(text, limit):
    """Shorten text to limit characters, marking the cut with an ellipsis."""
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _describe(call):
    """Render a tool call as name(clipped JSON arguments), secrets redacted."""
    args = credentials.redact(json.dumps(call["args"], ensure_ascii=False))
    return f"{call['name']}({_clip(args, ARGS_CLIP)})"


def _dim(text):
    """Dim text with ANSI codes when stdout is a terminal."""
    return f"{DIM}{text}{RESET}" if sys.stdout.isatty() else text


def print_event(kind, payload):
    """on_event for terminals: streamed or whole assistant text, tool lines, dim results."""
    if kind == "text_delta":
        _streamed.append(payload["text"])
        done, newline, rest = "".join(_streamed).rpartition("\n")
        if newline:  # keys never span lines, so redacting whole lines is exact
            print(credentials.redact(done + newline), end="")
            _streamed[:] = [rest] if rest else []
    elif kind == "assistant":
        if not payload.get("streamed"):
            if payload["text"]:
                print(credentials.redact(payload["text"]))
        elif _streamed:
            print(credentials.redact("".join(_streamed)))
            _streamed.clear()
    elif kind == "tool_start":
        print(f"-> {_describe(payload)}")
    elif kind == "tool_end":
        first = credentials.redact((payload["result"].splitlines() or [""])[0])
        print(_dim(f"   {_clip(first, RESULT_CLIP)}"))
    elif kind == "loop_detected":
        action = "stopping" if payload["stopped"] else "warned the model"
        print(_dim(f"loop: {payload['name']} repeated {payload['count']}x, {action}"))
    elif kind == "todo":
        print(_dim(credentials.redact(payload["items"])))
    elif kind == "verify":
        outcome = "not run" if payload["exit"] is None else f"exit {payload['exit']}"
        print(_dim(f"verify: {payload['command']} -> {outcome}"))
    elif kind == "present":
        print(f"presented: {', '.join(f['path'] for f in payload['files'])}")
    elif kind == "goal" and payload:
        print(_dim(credentials.redact(goal.describe(payload))))
    elif kind == "plan":
        print(_dim("plan approved; plan mode is off"))
    sys.stdout.flush()


_asking = threading.Lock()  # sub-agents on other threads must not interleave prompts


def ask_approval(call, reason):
    """Policy approver: show the call and return True only on an explicit yes."""
    with _asking:
        if "untrusted" in reason:  # an unusual request says why; routine ones stay terse
            print(_dim(f"   {reason}"))
        try:
            answer = input(f"approve {_describe(call)}? [y/N] ")
        except EOFError:
            return False
    return answer.strip().lower() in ("y", "yes")


def ask_user(request):
    """Harness asker: prompt for a question's answer or a plan's approval; None if skipped."""
    with _asking:
        try:
            if request["kind"] == "plan":
                print(f"\nplan for your review:\n{credentials.redact(request['plan'])}")
                answer = input("approve it? [y = yes, Enter = no, or type what to change] ")
                yes = answer.strip().lower() in ("y", "yes")
                return {"approved": yes, "feedback": "" if yes else answer.strip()}
            header = f"[{request['header']}] " if request.get("header") else ""
            print(f"\n{header}{credentials.redact(request['question'])}")
            options = request["options"]
            for number, option in enumerate(options, 1):
                detail = f" - {option['description']}" if option.get("description") else ""
                print(f"  {number}. {option['label']}{detail}")
            hint = "numbers, comma-separated" if request["multi_select"] else "a number"
            answer = input(f"answer ({hint}, or type your own): " if options else "answer: ")
        except EOFError:
            return None
    picks = [p.strip() for p in answer.split(",")]
    if (options and all(p.isdigit() and 1 <= int(p) <= len(options) for p in picks)
            and (request["multi_select"] or len(picks) == 1)):
        return ", ".join(options[int(p) - 1]["label"] for p in picks)
    return answer.strip() or None


def build_parser():
    """Return the argument parser for `zill [task]` and `zill run task`."""
    parser = argparse.ArgumentParser(
        prog="zill", description="ZILL: a concise coding-agent harness.",
        epilog=f"commands: run, resume, {', '.join(commands.COMMANDS)} "
               f"(zill COMMAND --help for each)")
    parser.add_argument("task", nargs="?", help="run this task headlessly and exit")
    parser.add_argument("-p", "--prompt", help="same as giving the task")
    parser.add_argument("-d", "--workdir", default=".", help="directory the agent works in")
    parser.add_argument("-m", "--model",
                        help="provider:model, e.g. anthropic:claude-opus-5, openai:gpt-5, "
                             "ollama:qwen3 (default: ZILL_MODEL, config, else the first "
                             "provider with a key)")
    parser.add_argument("--mode", choices=MODES,
                        help="tool policy (default: safe interactively, yolo headless)")
    parser.add_argument("--profile", choices=list(PROFILES),
                        help="preset for the kind of work (default: coding)")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and inspect only: every call that is not a read is blocked")
    parser.add_argument("--plan", action="store_true",
                        help="start in plan mode: reads only until you approve the agent's plan")
    parser.add_argument("--goal", action="store_true",
                        help="treat the task as a goal and keep taking turns until it is done")
    parser.add_argument("--verify", metavar="COMMAND",
                        help="a run is done only when COMMAND passes, e.g. \"pytest -q\" "
                             "(default: \"verify\" in .zill/project.json)")
    parser.add_argument("--json", action="store_true",
                        help="headless: print one JSON result object and nothing else")
    parser.add_argument("--resume", action="store_true",
                        help="continue the latest session in the working directory")
    parser.add_argument("--max-turns", type=int, default=120, help="tool turns per task")
    return parser


def main(argv=None):
    """Dispatch a subcommand, or resolve settings and run headlessly or interactively."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # Tool results carry em dashes; a legacy console codepage must not crash us.
    if hasattr(sys.stdout, "reconfigure"):  # absent when stdout is redirected in-process
        sys.stdout.reconfigure(errors="replace")
    if argv[:1] and argv[0] in commands.COMMANDS:
        return commands.COMMANDS[argv[0]](argv[1:])
    parser = build_parser()
    run_command = argv[:1] == ["run"]
    if run_command or argv[:1] == ["resume"]:
        argv = argv[1:] if run_command else ["--resume", *argv[1:]]
    args = parser.parse_args(argv)
    task = args.prompt or args.task
    if run_command and not task:
        parser.error("zill run needs a task, e.g. zill run \"add a --json flag\"")
    quiet = args.json
    try:
        resolved = settings.resolve(args.workdir, args.model, args.mode, args.profile,
                                    headless=bool(task))
        model = resolved["model"]
        problem = provider.check_model(model) or (
            provider.missing_key(model) and f"No API key found for {model}")
        if problem and not task and sys.stdin.isatty():
            print(f"{problem}. Let's fix that.")
            if commands.setup():
                return 1
            resolved = settings.resolve(args.workdir, args.model, args.mode, args.profile)
        # A bad model name is reported by make_harness, not as a missing key.
        model = resolved["model"]
        missing = not provider.check_model(model) and provider.missing_key(model)
        if missing:
            raise RuntimeError(f"no API key for {resolved['model']}. "
                               f"Run `zill setup`, or set {missing}.")
        harness = settings.make_harness(
            resolved, approver=None if quiet else ask_approval, dry_run=args.dry_run,
            plan=args.plan, asker=None if quiet else ask_user,
            on_event=None if quiet else print_event, max_turns=args.max_turns,
            verify=args.verify, stream=not quiet and sys.stdout.isatty())
    except RuntimeError as err:
        return _fail(err, quiet)
    if not quiet:
        for note in resolved["notes"] + harness.notes:
            print(f"note: {note}", file=sys.stderr)
    try:
        if args.resume and not harness.resume() and not quiet:
            print("no session to resume; starting fresh", file=sys.stderr)
        if task:
            return _headless(harness, task, quiet, args.goal)
        return _interactive(harness)
    finally:
        harness.close()


def _fail(err, as_json):
    """Report an error for people or scripts; return exit code 1."""
    message = credentials.redact(str(err))
    if as_json:
        print(json.dumps({"ok": False, "error": message}))
    else:
        print(f"error: {message}", file=sys.stderr)
    return 1


def _prices():
    """Return user price overrides, or {} if the user config cannot be read."""
    try:
        return config.load_user().get("prices", {})
    except RuntimeError:
        return {}


def _headless(harness, task, as_json, as_goal=False):
    """Run one task (or pursue it as a goal); print its result and a cost line; return exit code."""
    try:
        result = harness.pursue(task) if as_goal else harness.run(task)
    except RuntimeError as err:  # provider failures: a message, not a traceback
        return _fail(err, as_json)
    if as_json:
        print(json.dumps({"ok": True, "result": credentials.redact(result),
                          "model": harness.model, "session": harness.session_path,
                          "usage": harness.usage, "todo": harness.todo, "goal": harness.goal,
                          "cost_usd": cost.estimate(harness.model, harness.usage, _prices())},
                         ensure_ascii=False))
    else:
        print(_dim(cost.describe(harness.model, harness.usage, _prices())), file=sys.stderr)
    return 0


def _slash(harness, line):
    """Handle one /command; return False when the session should end."""
    command, _, arg = line.partition(" ")
    arg = arg.strip()
    if command == "/exit":
        return False
    if command == "/help":
        print("\n".join(f"{name:<13} {text}" for name, text in SLASH.items()))
    elif command == "/cost":
        print(cost.describe(harness.model, harness.usage, _prices()))
    elif command == "/context":
        _show_context(harness)
    elif command == "/model":
        _pick_model(harness, arg)
        print(f"model: {harness.model}")
    elif command == "/mode":
        if arg and arg not in MODES:
            print(f"mode must be one of {', '.join(MODES)}")
        elif arg:
            harness.policy.mode = arg
        print(f"mode: {harness.policy.mode}{' (dry-run)' if harness.policy.dry_run else ''}")
    elif command == "/compact":
        before, after = harness.compact()
        print(f"compacted {before} messages to {after}" if after < before
              else "nothing to compact yet")
    elif command == "/clear":
        harness.clear()
        print("started a fresh conversation")
    elif command == "/todo":
        print(harness.todo or "no todo list yet")
    elif command == "/sessions":
        commands.sessions(["-d", harness.workdir])
    elif command == "/skills":
        catalog = skills.catalog(harness.workdir)
        print("\n".join(f"{name}: {info['description']}" for name, info in catalog.items())
              or "no skills: add skills/<name>/SKILL.md")
    elif command in ("/undo", "/checkpoints"):
        if not (harness.checkpoints and harness.checkpoints.available):
            print("checkpoints are unavailable (git is not on PATH)")
        elif command == "/undo":
            commands.restore(harness.checkpoints)
        else:
            commands.checkpoints(["-d", harness.workdir])
    elif command in ("/plan", "/goal", "/retry", "/branch", "/jobs", "/kill", "/search",
                     "/feedback"):
        _work_command(harness, command, arg)
    else:
        sub = command[1:]
        print(f"`{sub}` is a terminal command: /exit, then run `zill {sub}`"
              if sub in commands.COMMANDS else f"unknown command {command}; try /help")
    return True


def _work_command(harness, command, arg):
    """Handle the slash commands for plans, goals, turns, background work and history."""
    turns = len(harness.turn_starts(harness.transcript()))
    if command == "/plan":
        if arg in ("on", "off"):
            harness.policy.plan = arg == "on"
        print(f"plan mode: {'on' if harness.policy.plan else 'off'}")
    elif command == "/goal" and arg == "clear":
        harness.goal = None
        print("goal cleared")
    elif command == "/goal":
        if arg or (harness.goal and harness.goal["status"] == "active"):
            harness.pursue(arg or None)
        print(goal.describe(harness.goal))
    elif command in ("/retry", "/branch", "/feedback") and not turns:
        print("nothing to work with yet: send a message first")
    elif command == "/retry":
        harness.run(harness.rewind(turns - 1))
    elif command == "/branch":
        print(f"continuing in a new session: {harness.branch(turns - 1)}")
    elif command == "/jobs":
        rows = harness.processes.rows() + [dict(r, kind="agent", command=r["name"])
                                           for r in (harness.agents.rows() if harness.agents else [])]
        print("\n".join(f"{r['id']:<5} {r['kind']:<9} {r['status']:<12} {r['command']}"
                        for r in rows) or "no jobs, terminals or sub-agents")
    elif command == "/kill":
        items = {**harness.processes.jobs, **harness.processes.terminals}
        if arg in items:
            _kill(items[arg]["proc"])
        elif harness.agents and arg in harness.agents.agents:
            harness.agents.agents[arg]["harness"].stop()
        print(f"stopped {arg}" if arg in items or (harness.agents and arg in harness.agents.agents)
              else "usage: /kill ID (see /jobs)")
    elif command == "/search":
        hits = history.search(harness.workdir, arg) if arg else []
        print("\n".join(credentials.redact(f"{h['session']}  {h['role']}: {h['snippet']}")
                        for h in hits) or "usage: /search TEXT (no matches)")
    elif command == "/feedback":
        rating, _, note = arg.partition(" ")
        if rating not in ("good", "bad") or not harness.session_path:
            print("usage: /feedback good | bad [note]")
            return
        history.feedback(harness.workdir, harness.audit_label(), turns - 1, rating, note)
        print("thanks: saved to .zill/feedback.jsonl")


def _percent(used):
    """Return the share of the window the next request fills, as a percentage."""
    return 100 * used["total"] / used["window"]


def _show_context(harness):
    """Print a fill bar and the estimated tokens held by each part of the next request."""
    used = harness.context_usage()
    filled = min(BAR, round(BAR * used["total"] / used["window"]))
    print(f"context  {harness.model}\n"
          f"  [{'#' * filled}{'.' * (BAR - filled)}] {_percent(used):.1f}% "
          f"of {used['window']:,} tokens (estimated)")
    rows = [("system prompt", used["system"]), (f"tools ({len(harness.tools)})", used["tools"]),
            (f"messages ({len(harness.messages)})", used["messages"]),
            ("total", used["total"]), ("free", max(0, used["window"] - used["total"]))]
    for label, tokens in rows:
        print(f"  {label:<16}{tokens:>12,}")
    print(f"  auto-compact once messages pass {used['compact_at']:,} tokens (/compact to force)")
    if used["reported"]:
        print(f"  last request: {used['reported']:,} input tokens, as reported by the provider")


def _model_choices(current):
    """Return the models /model lists: each provider's default, then current if it is not one.

    The defaults come first so a number means the same model before and after a switch.
    """
    choices = [f"{name}:{default}" for name, (_, _, _, default) in provider.PROVIDERS.items()
               if default]
    return choices if current in choices else choices + [current]


def _pick_model(harness, arg):
    """Switch to arg (a list number or provider:model); with no arg, list and ask."""
    choices = _model_choices(harness.model)
    if not arg:
        for number, model in enumerate(choices, 1):
            missing = provider.missing_key(model)
            print(f"{'*' if model == harness.model else ' '} {number}. {model}"
                  f"{f'  (needs {missing})' if missing else ''}")
        print("  or any provider:model, e.g. ollama:qwen3, openrouter:MODEL")
        if not sys.stdin.isatty():
            return
        try:
            arg = input("number or name (Enter keeps the current model): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not arg:
            return
    if arg.isdigit():
        if not 1 <= int(arg) <= len(choices):
            print(f"choose a number from 1 to {len(choices)}")
            return
        arg = choices[int(arg) - 1]
    problem = provider.check_model(arg)
    missing = not problem and provider.missing_key(arg)
    if problem or missing:
        print(problem or f"{arg} needs {missing}: run `zill setup` first")
        return
    harness.set_model(arg)
    used = harness.context_usage()
    if used["messages"] > used["compact_at"]:
        print(f"note: this conversation (~{used['messages']:,} tokens) is past {arg}'s "
              f"compaction point, so it will be summarised before the next turn")


def _interactive(harness):
    """Prompt loop: each line is a task or a /command; Ctrl-D exits, Ctrl-C stops a run."""
    dry = "  dry-run" if harness.policy.dry_run else ""
    print(f"ZILL Harness  model={harness.model}  mode={harness.policy.mode}{dry}\n"
          f"jail: {harness.workdir}\nCtrl-C interrupts a run. Type /help for commands.")
    if harness.messages:
        print(f"resumed {len(harness.messages)} messages from {harness.session_path}")
    while True:
        try:
            meter = f"zill [{_percent(harness.context_usage()):.0f}%]> "
            # A Windows pipe can open with a byte-order mark that would hide a leading "/".
            task = input(meter if harness.messages else PROMPT).lstrip("﻿").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not task:
            continue
        try:
            if task.startswith("/"):
                if not _slash(harness, task):
                    return 0
                continue
            harness.run(task)
            if harness.goal and harness.goal["status"] == "active":  # the model set a goal
                harness.pursue()
        except KeyboardInterrupt:
            harness.stop()  # background sub-agents stop too
            print(f"\ninterrupted. The session log is safe: {harness.session_path}\n"
                  f"Keep typing to continue here, or run with --resume later.")
        except Exception as err:  # a failed API call ends the run, not the session
            print(f"error: {type(err).__name__}: {credentials.redact(str(err))}",
                  file=sys.stderr)
        else:
            continue
        _streamed.clear()
        if harness.session_path:
            harness.resume(harness.session_path)  # reload and repair cut-off calls
