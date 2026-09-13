"""Hooks: project commands that run around state-changing tool calls.

Concept: a hook pairs a tool pattern with a shell command. "after" hooks keep
work tidy (format the file the agent just wrote); "before" hooks guard it
(refuse edits to generated files). Hooks are declared in .zill/project.json.

Design rules:
  * Hooks match state-changing calls only, never reads: by tool name (glob,
    default "*") and, when the call has a path, by file glob (default "*").
  * {path} is substituted only when the path is plain (letters, digits,
    _ . / \\ -). A model-chosen path can never inject shell syntax.
  * A failing before-hook blocks the call and tells the model why. A failing
    after-hook appends its output to the tool result so the model can fix
    what it broke. A quiet success adds nothing to the context.
  * run_command is shared with verify: shell, workdir, timeout, bounded output.
"""

import fnmatch
import re
import subprocess

COMMAND_TIMEOUT = 300
OUTPUT_CHARS = 4000
SAFE_PATH = re.compile(r"[\w./\\-]+")


def matching(hooks, when, call):
    """Return the hooks for when ("before"/"after") that match call."""
    path = str(call["args"].get("path", "")).replace("\\", "/")
    return [h for h in hooks if h["when"] == when
            and fnmatch.fnmatch(call["name"], h.get("tool", "*"))
            and fnmatch.fnmatch(path, h.get("match", "*"))]


def command_for(hook, call):
    """Return hook's command with {path} filled in, or None if the path is unsafe."""
    command = hook["run"]
    if "{path}" in command:
        path = str(call["args"].get("path", ""))
        if not SAFE_PATH.fullmatch(path):
            return None
        command = command.replace("{path}", path)
    return command


def run_command(command, workdir, timeout=COMMAND_TIMEOUT):
    """Run command through the shell in workdir; return (exit code, bounded output)."""
    try:
        proc = subprocess.run(command, shell=True, cwd=workdir, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if len(output) > OUTPUT_CHARS:  # the end of a test run is where the failures are
        output = "... [earlier output clipped]\n" + output[-OUTPUT_CHARS:]
    return proc.returncode, output
