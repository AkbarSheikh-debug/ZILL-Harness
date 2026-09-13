"""Day 5 — The command line: the front door to the harness.

Concept: everything the week built is reachable from one command. With -p the
agent takes a task headlessly and exits; without it the user gets a prompt
loop over one persistent session. Both paths build the same Harness, so the
terminal adds only what a person needs to see and approve.

Design rules:
  * Presentation only. The CLI prints events and answers approvals; tools,
    policy, persistence and compaction stay in their own modules.
  * The default mode follows who is watching: safe when a person can answer
    approval prompts, yolo when -p runs with nobody there to answer them.
  * Ctrl-C stops the run, never the session. The log is flushed as it grows,
    so the interrupted transcript is reloaded (and its cut-off calls repaired)
    before the next prompt, and --resume picks it up in a later process.
  * Output is bounded: one line per tool call, one dimmed line per result.
  * Nothing reaches the terminal unredacted: known key values are masked in
    assistant text, tool calls, tool results and errors.
  * `zill setup` asks for keys with hidden input. An interactive start with
    no usable key runs it first, so installing and pasting a key is enough.
"""

import argparse
import getpass
import json
import sys

from . import credentials, provider
from .checkpoints import Checkpoints
from .harness import Harness
from .security import MODES, Policy

ARGS_CLIP = 100  # characters of a tool call's JSON arguments shown
RESULT_CLIP = 120  # characters of a result's first line shown
DIM, RESET = "\033[2m", "\033[0m"
PROMPT = "zill> "
COMMANDS = ("/undo", "/checkpoints", "/todo")


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
    """on_event for terminals: assistant text, tool call lines, dimmed results."""
    if kind == "assistant" and payload["text"]:
        print(credentials.redact(payload["text"]))
    elif kind == "tool_start":
        print(f"-> {_describe(payload)}")
    elif kind == "tool_end":
        first = credentials.redact((payload["result"].splitlines() or [""])[0])
        print(_dim(f"   {_clip(first, RESULT_CLIP)}"))
    elif kind == "todo":
        print(_dim(credentials.redact(payload["items"])))
    elif kind == "verify":
        outcome = "not run" if payload["exit"] is None else f"exit {payload['exit']}"
        print(_dim(f"verify: {payload['command']} -> {outcome}"))
    sys.stdout.flush()


def ask_approval(call, reason):
    """Policy approver: show the call and return True only on an explicit yes."""
    try:
        answer = input(f"approve {_describe(call)}? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def build_parser():
    """Return the argument parser for python -m zill."""
    parser = argparse.ArgumentParser(prog="zill",
                                     description="ZILL: a concise coding-agent harness.")
    parser.add_argument("-p", "--prompt", help="run this task headlessly and exit")
    parser.add_argument("-d", "--workdir", default=".", help="directory the agent is jailed to")
    parser.add_argument("-m", "--model",
                        help="provider:model, e.g. anthropic:claude-opus-5, openai:gpt-5, "
                             "ollama:qwen3 (default: ZILL_MODEL, else the first provider "
                             "with a key)")
    parser.add_argument("--mode", choices=MODES,
                        help="tool policy (default: safe interactively, yolo with -p)")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and inspect only: every call that is not a read is blocked")
    parser.add_argument("--verify", metavar="COMMAND",
                        help="a run is done only when COMMAND passes, e.g. \"pytest -q\" "
                             "(default: \"verify\" in .zill/project.json)")
    parser.add_argument("--resume", action="store_true",
                        help="continue the latest session in the working directory")
    parser.add_argument("--max-turns", type=int, default=120, help="tool turns per task")
    return parser


def checkpoint_command(argv):
    """`zill checkpoints` lists snapshots; `zill undo [ID]` restores one."""
    parser = argparse.ArgumentParser(prog=f"zill {argv[0]}")
    if argv[0] == "undo":
        parser.add_argument("id", nargs="?",
                            help="checkpoint to restore (default: undo the latest change)")
    parser.add_argument("-d", "--workdir", default=".", help="project directory")
    args = parser.parse_args(argv[1:])
    store = Checkpoints(args.workdir)
    if argv[0] == "checkpoints":
        entries = store.list()
        for ref, age, label in entries:
            print(f"{ref}  {age:>16}  {label}")
        if not entries:
            print("no checkpoints yet")
        return 0
    return _undo(store, args.id)


def _undo(store, ref=None):
    """Restore a checkpoint and report it; return an exit code."""
    try:
        restored = store.restore(ref)
    except RuntimeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(f"restored checkpoint {restored}. To reverse this, run `zill checkpoints` "
          f"and restore the \"restore:\" entry.")
    return 0


def setup():
    """Ask for each provider's API key with hidden input and save the ones given."""
    print(f"ZILL setup: paste an API key for each provider you use (input is hidden; "
          f"Enter skips).\nKeys are saved to {credentials.path()}")
    try:
        for name, (_, _, key_var, _) in provider.PROVIDERS.items():
            if key_var:
                status = " [already set]" if credentials.get(key_var) else ""
                value = getpass.getpass(f"  {name} {key_var}{status}: ").strip()
                if value:
                    credentials.save(key_var, value)
        suggested = provider.default_model()
        model = input(f"Default model [{suggested}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nsetup stopped; keys entered so far are saved")
        return 1
    if model:
        credentials.save("ZILL_MODEL", model)
    print(f"Ready. Model: {model or suggested}. Run `zill` to start.")
    return 0


def main(argv=None):
    """Parse arguments, then set up, run headlessly, or run interactively."""
    argv = sys.argv[1:] if argv is None else argv
    # Tool results carry em dashes; a legacy console codepage must not crash us.
    if hasattr(sys.stdout, "reconfigure"):  # absent when stdout is redirected in-process
        sys.stdout.reconfigure(errors="replace")
    if argv[:1] == ["setup"]:
        return setup()
    if argv[:1] in (["checkpoints"], ["undo"]):
        return checkpoint_command(argv)
    args = build_parser().parse_args(argv)
    model = args.model or provider.default_model()
    missing = provider.missing_key(model)
    if missing and not args.prompt and sys.stdin.isatty():
        print(f"No API key found for {model}. Let's add one.")
        setup()
        model = args.model or provider.default_model()
        missing = provider.missing_key(model)
    if missing:
        print(f"error: no API key for {model}. Run `zill setup`, or set {missing}.",
              file=sys.stderr)
        return 1
    mode = args.mode or ("yolo" if args.prompt else "safe")
    policy = Policy(mode, approver=ask_approval, dry_run=args.dry_run)
    try:
        harness = Harness(args.workdir, model=model, on_event=print_event, policy=policy,
                          max_turns=args.max_turns, verify=args.verify)
    except RuntimeError as err:  # e.g. an invalid .zill/project.json
        print(f"error: {err}", file=sys.stderr)
        return 1
    if args.resume and not harness.resume():
        print("no session to resume; starting fresh", file=sys.stderr)
    if args.prompt:
        try:
            harness.run(args.prompt)
        except RuntimeError as err:  # provider failures: a message, not a traceback
            print(f"error: {credentials.redact(str(err))}", file=sys.stderr)
            return 1
        return 0
    return _interactive(harness, mode)


def _slash(harness, command):
    """Handle an interactive command: /undo, /checkpoints or /todo."""
    if command == "/todo":
        print(harness.todo or "no todo list yet")
    elif not (harness.checkpoints and harness.checkpoints.available):
        print("checkpoints are unavailable (git is not on PATH)")
    elif command == "/checkpoints":
        for ref, age, label in harness.checkpoints.list() or [("", "", "no checkpoints yet")]:
            print(f"{ref}  {age}  {label}".strip())
    else:
        _undo(harness.checkpoints)


def _interactive(harness, mode):
    """Prompt loop: each line is a task; Ctrl-D exits, Ctrl-C stops the current run."""
    dry = "  dry-run" if harness.policy.dry_run else ""
    print(f"ZILL Harness  model={harness.model}  mode={mode}{dry}\n"
          f"jail: {harness.workdir}\nCtrl-D exits, Ctrl-C interrupts a run. "
          f"Commands: {' '.join(COMMANDS)}")
    if harness.messages:
        print(f"resumed {len(harness.messages)} messages from {harness.session_path}")
    while True:
        try:
            task = input(PROMPT).strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not task:
            continue
        if task in COMMANDS:
            _slash(harness, task)
            continue
        try:
            harness.run(task)
        except KeyboardInterrupt:
            print(f"\ninterrupted. The session log is safe: {harness.session_path}\n"
                  f"Keep typing to continue here, or run with --resume later.")
        except Exception as err:  # a failed API call ends the run, not the session
            print(f"error: {type(err).__name__}: {credentials.redact(str(err))}",
                  file=sys.stderr)
        else:
            continue
        if harness.session_path:
            harness.resume(harness.session_path)  # reload and repair cut-off calls
