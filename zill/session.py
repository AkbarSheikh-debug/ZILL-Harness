"""Day 4 — Sessions: a transcript on disk that outlives the process.

Concept: the message list is the agent's only state (day 1), so persisting
that list is persisting the agent. Every message is appended to a JSONL file
the moment it exists; after a crash, load() reads the file back and the loop
continues as if the process had never died.

Design rules:
  * Append-only, one JSON object per line. A write never rewrites earlier
    lines, so a crash can damage at most the final line.
  * A line that fails to parse is a torn tail: everything before it is kept,
    it and anything after it are dropped.
  * A loaded transcript always satisfies the pairing rule — every tool call
    of the last assistant message has a tool result. Calls the crash cut off
    get an explicit "interrupted" result, so the model knows they never ran.
"""

import glob
import json
import os
import re
import time

SESSION_DIR = ".zill/sessions"
SLUG_CHARS = 40
INTERRUPTED = "Interrupted before this ran (process restarted)."


def new_session(workdir, label="session"):
    """Create the session directory and return a fresh session file path."""
    base = os.path.join(workdir, SESSION_DIR)
    os.makedirs(base, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")[:SLUG_CHARS].strip("-")
    return os.path.join(base, f"{int(time.time())}-{slug or 'session'}.jsonl")


def append(path, message):
    """Write message to the session file as one JSON line."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")


def load(path):
    """Read a session file, drop any torn tail, and repair unanswered calls."""
    messages = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                break  # torn tail: the process died mid-write
    return _repair(messages)


def latest(workdir):
    """Return the newest session file in workdir, or None."""
    paths = glob.glob(os.path.join(workdir, SESSION_DIR, "*.jsonl"))
    return max(paths, key=os.path.getmtime) if paths else None


def _repair(messages):
    """Answer each tool call of the last assistant message that has no result."""
    last = next((i for i in range(len(messages) - 1, -1, -1)
                 if messages[i]["role"] == "assistant"), None)
    if last is None:
        return messages
    answered = sum(1 for m in messages[last + 1:] if m["role"] == "tool")
    for call in (messages[last].get("tool_calls") or [])[answered:]:
        messages.append({"role": "tool", "name": call["name"], "text": INTERRUPTED})
    return messages
