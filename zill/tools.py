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
  * Output is bounded: big listings are truncated with a note, and the harness
    passes every result through bound_result(), which keeps a head and tail
    and saves the full text under .zill/spill, so no output is lost and one
    call cannot flood the context window.
  * bash always reports how a command ended: stderr in its own section and a
    non-zero exit code on the last line, even when the command printed output.
"""

import fnmatch
import hashlib
import inspect
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

# shell=True runs COMSPEC on Windows and /bin/sh elsewhere; the prompt names it.
SHELL = os.environ.get("COMSPEC", "cmd.exe") if os.name == "nt" else "/bin/sh"
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
MAX_READ_LINES = 4000
MAX_RESULT_CHARS = 12000  # most a tool result may put into the conversation
MAX_BASH_CHARS = 200_000  # a memory bound only; bound_result shortens what the model sees
SPILL_DIR = ".zill/spill"
MAX_LIST_ENTRIES = 500
MAX_GREP_HITS = 200
MAX_GREP_LINE = 200


@dataclass
class Tool:
    """A callable the model may invoke: spec is {"schema": ...}, run executes it.

    source says where it came from (builtin, mcp, plugin, connector) and risk
    what it can do (see security.RISKS); Policy reads both, the model neither.
    """

    name: str
    spec: dict
    run: Callable
    source: str = "builtin"
    risk: str = "execute"


def tool(description, risk="execute", source="builtin", **params):
    """Turn a plain function into a Tool whose schema mirrors its signature.

    params maps each argument name to the description the model sees.
    Arguments with defaults are optional; the rest are required. Undeclared
    risk is "execute", so an unknown tool needs approval in safe mode.
    """
    def wrap(fn):
        signature = inspect.signature(fn).parameters.values()
        properties = {p.name: {"type": "string", "description": params.get(p.name, "")}
                      for p in signature}
        required = [p.name for p in signature if p.default is inspect.Parameter.empty]
        schema = {"name": fn.__name__, "description": description,
                  "parameters": {"type": "object", "properties": properties,
                                 "required": required}}
        return Tool(name=fn.__name__, spec={"schema": schema}, run=fn, source=source, risk=risk)
    return wrap


def bound_result(workdir, name, text):
    """Return text, or its head and tail with a note, fitting MAX_RESULT_CHARS.

    The full text is saved under SPILL_DIR and the note names the file. The
    file name is a hash of the text, so a repeated result gets the same path
    and the loop still recognises the repeat. read_file results are not saved,
    because the file is already on disk; their note says to read a narrower
    range. If the save fails, the full text is returned rather than lost.
    """
    if len(text) <= MAX_RESULT_CHARS:
        return text
    if name == "read_file":
        hint = "read a narrower range with offset and limit"
    else:
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        rel = f"{SPILL_DIR}/{re.sub(r'[^A-Za-z0-9_-]', '_', name)[:40]}-{digest}.txt"
        try:
            os.makedirs(os.path.join(workdir, SPILL_DIR), exist_ok=True)
            with open(os.path.join(workdir, rel), "w", encoding="utf-8", errors="replace",
                      newline="") as f:
                f.write(text)
        except OSError:
            return text
        hint = f"full output saved to {rel}; read_file it with offset and limit, or grep it"
    note = "\n... [{} chars omitted: " + hint + "] ...\n"
    keep = MAX_RESULT_CHARS - len(note.format(len(text)))  # the note fits inside the cap
    head = keep // 2
    return text[:head] + note.format(len(text) - keep) + text[len(text) - (keep - head):]


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

    @tool("Read a text file; lines come back numbered as N<TAB>line.", risk="read",
          path="File path relative to the working directory",
          offset="Line number to start from (default 1)",
          limit=f"Most lines to return (default and maximum {MAX_READ_LINES})")
    def read_file(path, offset="1", limit=str(MAX_READ_LINES)):
        with open(resolve(path), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        start, count = max(int(offset), 1), min(max(int(limit), 1), MAX_READ_LINES)
        if lines and start > len(lines):
            return f"ERROR: offset {start} is past the end of {path} ({len(lines)} lines)"
        chosen = lines[start - 1:start - 1 + count]
        shown = "\n".join(f"{i}\t{line}" for i, line in enumerate(chosen, start))
        end = start + len(chosen) - 1
        if start > 1 or end < len(lines):
            more = f"; continue with offset {end + 1}" if end < len(lines) else ""
            shown += f"\n... showing lines {start}-{end} of {len(lines)}{more}"
        return shown

    @tool("Create or overwrite a file with the given content.", risk="write",
          path="File path relative to the working directory",
          content="The complete new file content")
    def write_file(path, content):
        full = resolve(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {path}"

    @tool("Replace one exact snippet in a file. The snippet must occur exactly once.", risk="write",
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
        parts = [proc.stdout.rstrip("\n")] if proc.stdout else []
        if proc.stderr:
            parts.append("[stderr]\n" + proc.stderr.rstrip("\n"))
        output = "\n".join(parts)
        if len(output) > MAX_BASH_CHARS:
            half = MAX_BASH_CHARS // 2
            cut = len(output) - MAX_BASH_CHARS
            output = f"{output[:half]}\n... [{cut} chars truncated] ...\n{output[-half:]}"
        if proc.returncode != 0:  # last, so it survives any head-and-tail cut
            output = "\n".join(filter(None, [output, f"[exit code: {proc.returncode}]"]))
        return output or "(no output)"

    @tool("List files whose relative path or basename matches a glob.", risk="read",
          pattern="Glob such as **/*.py or *.md (default **/*)")
    def list_files(pattern="**/*"):
        hits = sorted(rel for rel, _ in walk() if matches(rel, pattern))
        if len(hits) > MAX_LIST_ENTRIES:
            extra = len(hits) - MAX_LIST_ENTRIES
            hits = hits[:MAX_LIST_ENTRIES] + [f"... and {extra} more"]
        return "\n".join(hits) or "(no files match)"

    @tool("Search file contents with a regular expression; returns path:lineno: text.", risk="read",
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
