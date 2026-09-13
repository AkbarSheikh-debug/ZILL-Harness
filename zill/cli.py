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
"""

import argparse
import json
import sys

from .harness import Harness
from .security import MODES, Policy

ARGS_CLIP = 100  # characters of a tool call's JSON arguments shown
RESULT_CLIP = 120  # characters of a result's first line shown
DIM, RESET = "\033[2m", "\033[0m"
PROMPT = "zill> "


def _clip(text, limit):
    """Shorten text to limit characters, marking the cut with an ellipsis."""
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _describe(call):
    """Render a tool call as name(clipped JSON arguments)."""
    return f"{call['name']}({_clip(json.dumps(call['args'], ensure_ascii=False), ARGS_CLIP)})"


def _dim(text):
    """Dim text with ANSI codes when stdout is a terminal."""
    return f"{DIM}{text}{RESET}" if sys.stdout.isatty() else text


def print_event(kind, payload):
    """on_event for terminals: assistant text, tool call lines, dimmed results."""
    if kind == "assistant" and payload["text"]:
        print(payload["text"])
    elif kind == "tool_start":
        print(f"-> {_describe(payload)}")
    elif kind == "tool_end":
        first = (payload["result"].splitlines() or [""])[0]
        print(_dim(f"   {_clip(first, RESULT_CLIP)}"))
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
    parser.add_argument("-m", "--model", help="model name (default: ZILL_MODEL or built-in)")
    parser.add_argument("--mode", choices=MODES,
                        help="tool policy (default: safe interactively, yolo with -p)")
    parser.add_argument("--resume", action="store_true",
                        help="continue the latest session in the working directory")
    parser.add_argument("--max-turns", type=int, default=120, help="tool turns per task")
    return parser


def main(argv=None):
    """Parse arguments, then run headlessly or interactively; return an exit code."""
    args = build_parser().parse_args(argv)
    # Tool results carry em dashes; a legacy console codepage must not crash us.
    sys.stdout.reconfigure(errors="replace")
    mode = args.mode or ("yolo" if args.prompt else "safe")
    harness = Harness(args.workdir, model=args.model, on_event=print_event,
                      policy=Policy(mode, approver=ask_approval), max_turns=args.max_turns)
    if args.resume and not harness.resume():
        print("no session to resume; starting fresh", file=sys.stderr)
    if args.prompt:
        try:
            harness.run(args.prompt)
        except RuntimeError as err:  # provider failures: a message, not a traceback
            print(f"error: {err}", file=sys.stderr)
            return 1
        return 0
    return _interactive(harness, mode)


def _interactive(harness, mode):
    """Prompt loop: each line is a task; Ctrl-D exits, Ctrl-C stops the current run."""
    print(f"ZILL Harness  model={harness.model}  mode={mode}\n"
          f"jail: {harness.workdir}\nCtrl-D exits, Ctrl-C interrupts a run.")
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
        try:
            harness.run(task)
        except KeyboardInterrupt:
            print(f"\ninterrupted. The session log is safe: {harness.session_path}\n"
                  f"Keep typing to continue here, or run with --resume later.")
        except Exception as err:  # a failed API call ends the run, not the session
            print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        else:
            continue
        if harness.session_path:
            harness.resume(harness.session_path)  # reload and repair cut-off calls
