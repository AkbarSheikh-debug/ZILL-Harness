"""Day 2 — Tools: plain functions the model can call, fenced into one directory.

Concept: a tool is a name, a JSON schema the model reads, and a callable the
harness runs. The @tool decorator derives the schema from the function's own
signature, so the description the model sees and the code that runs can
never drift apart.

Design rules:
  * Every parameter is string-typed. Models send strings reliably; each tool
    parses what it needs (e.g. bash's timeout) and raises on bad input.
  * Every path goes through one resolve(), which follows symlinks and refuses
    anything outside the working directory with a PermissionError. The loop
    turns that into an "ERROR: ..." result, never a crash.
  * Recoverable misuse (a bad edit snippet, a timeout) returns an "ERROR: ..."
    string that tells the model how to fix its next call.
  * Output is bounded: long files, long command output, and big listings are
    truncated with a note, so one call cannot flood the context window.
"""

import fnmatch
import inspect
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
MAX_READ_LINES = 4000
MAX_BASH_CHARS = 12000
MAX_LIST_ENTRIES = 500
MAX_GREP_HITS = 200
MAX_GREP_LINE = 200


@dataclass
class Tool:
    """A callable the model may invoke: spec is {"schema": ...}, run executes it."""

    name: str
    spec: dict
    run: Callable


def tool(description, **params):
    """Turn a plain function into a Tool whose schema mirrors its signature.

    params maps each argument name to the description the model sees.
    Arguments with defaults are optional; the rest are required.
    """
    def wrap(fn):
        signature = inspect.signature(fn).parameters.values()
        properties = {p.name: {"type": "string", "description": params.get(p.name, "")}
                      for p in signature}
        required = [p.name for p in signature if p.default is inspect.Parameter.empty]
        schema = {"name": fn.__name__, "description": description,
                  "parameters": {"type": "object", "properties": properties,
                                 "required": required}}
        return Tool(name=fn.__name__, spec={"schema": schema}, run=fn)
    return wrap


def core_tools(workdir):
    """Return the six file and shell tools, all confined to workdir."""
    root = os.path.realpath(workdir)

    def resolve(path):
        """Map a model-supplied path to a real path inside root, or refuse."""
        full = os.path.realpath(os.path.join(root, path))
        try:
            inside = os.path.commonpath([root, full]) == root
        except ValueError:  # different drives on Windows
            inside = False
        if not inside:
            raise PermissionError(f"{path!r} escapes the working directory")
        return full

    def walk():
        """Yield (relative posix path, real path) for every non-ignored file."""
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for name in filenames:
                full = os.path.join(dirpath, name)
                yield os.path.relpath(full, root).replace(os.sep, "/"), full

    def matches(rel, pattern):
        """True if pattern matches the relative path or the basename.

        fnmatch's "*" already spans "/", so a leading "**/" only adds a
        required slash; it is also tried without, letting "**/*.py" match
        top-level files.
        """
        candidates = [pattern] + ([pattern[3:]] if pattern.startswith("**/") else [])
        base = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(base, p) for p in candidates)

    @tool("Read a text file; lines come back numbered as N<TAB>line.",
          path="File path relative to the working directory")
    def read_file(path):
        with open(resolve(path), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        shown = "\n".join(f"{i}\t{line}" for i, line in enumerate(lines[:MAX_READ_LINES], 1))
        if len(lines) > MAX_READ_LINES:
            shown += f"\n... truncated: showing {MAX_READ_LINES} of {len(lines)} lines"
        return shown

    @tool("Create or overwrite a file with the given content.",
          path="File path relative to the working directory",
          content="The complete new file content")
    def write_file(path, content):
        full = resolve(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {path}"

    @tool("Replace one exact snippet in a file. The snippet must occur exactly once.",
          path="File path relative to the working directory",
          old="Exact text to replace, copied from the file",
          new="Replacement text")
    def edit_file(path, old, new):
        full = resolve(path)
        with open(full, encoding="utf-8", newline="") as f:
            text = f.read()
        count = text.count(old) if old else 0
        if count == 0:
            return "ERROR: snippet not found — read the file and copy it exactly"
        if count > 1:
            return f"ERROR: snippet appears {count} times — include more context to make it unique"
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new, 1))
        return f"Edited {path}"

    @tool("Run a shell command in the working directory; returns combined stdout and stderr.",
          command="The shell command to run",
          timeout="Seconds before the command is killed (default 120)")
    def bash(command, timeout="120"):
        seconds = float(timeout)
        try:
            proc = subprocess.run(command, shell=True, cwd=root, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=seconds)
        except subprocess.TimeoutExpired:
            return f"ERROR: timed out after {timeout}s"
        output = (proc.stdout or "") + (proc.stderr or "")
        if len(output) > MAX_BASH_CHARS:
            half = MAX_BASH_CHARS // 2
            cut = len(output) - MAX_BASH_CHARS
            output = f"{output[:half]}\n... [{cut} chars truncated] ...\n{output[-half:]}"
        return output or f"(exit {proc.returncode}, no output)"

    @tool("List files whose relative path or basename matches a glob.",
          pattern="Glob such as **/*.py or *.md (default **/*)")
    def list_files(pattern="**/*"):
        hits = sorted(rel for rel, _ in walk() if matches(rel, pattern))
        if len(hits) > MAX_LIST_ENTRIES:
            extra = len(hits) - MAX_LIST_ENTRIES
            hits = hits[:MAX_LIST_ENTRIES] + [f"... and {extra} more"]
        return "\n".join(hits) or "(no files match)"

    @tool("Search file contents with a regular expression; returns path:lineno: text.",
          regex="Python regular expression to search for",
          pattern="Glob limiting which files are searched (default *)")
    def grep(regex, pattern="*"):
        compiled = re.compile(regex)
        hits = []
        for rel, full in sorted(walk()):
            if not matches(rel, pattern):
                continue
            try:
                with open(full, encoding="utf-8") as f:
                    for lineno, line in enumerate(f, 1):
                        if compiled.search(line):
                            hits.append(f"{rel}:{lineno}: {line.rstrip()[:MAX_GREP_LINE]}")
                            if len(hits) >= MAX_GREP_HITS:
                                return "\n".join(hits) + f"\n... stopped at {MAX_GREP_HITS} hits"
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable: not searchable text
        return "\n".join(hits) or "(no matches)"

    return [read_file, write_file, edit_file, bash, list_files, grep]
