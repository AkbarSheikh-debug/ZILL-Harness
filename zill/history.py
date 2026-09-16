"""History: saved sessions as something to list, search, rename, delete and rate.

Concept: every conversation is already a JSONL file under .zill/sessions.
History turns that folder into what a person and the agent need from past
work: a list with titles, a text search (session_search, for the model too),
renames and deletes, and a feedback log of good and bad replies.

Design rules:
  * A session id is its file name without .jsonl, and only ids made of
    letters, digits, "-", "_" and "." are accepted, so no id can name a path.
  * Titles live in the sidecar .meta.json; the transcript is never rewritten.
  * Feedback is appended to .zill/feedback.jsonl, one rating per line.
  * Search reads user and assistant text and tool arguments, case-insensitive,
    and returns bounded snippets.
"""

import glob
import json
import os
import re
import time

from . import session
from .tools import tool

SESSION_ID = re.compile(r"[\w.-]+")
FEEDBACK_FILE = ".zill/feedback.jsonl"
SNIPPET = 160
MAX_RESULTS = 30


def path_for(workdir, session_id):
    """Return the transcript path of session_id in workdir, or raise ValueError."""
    path = os.path.join(os.path.realpath(workdir), session.SESSION_DIR, f"{session_id}.jsonl")
    if not SESSION_ID.fullmatch(str(session_id)) or not os.path.isfile(path):
        raise ValueError(f"no session named {session_id}")
    return path


def meta(path):
    """Return a session's sidecar metadata, or {}."""
    try:
        with open(f"{os.path.splitext(path)[0]}.meta.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def session_rows(workdir):
    """Describe each saved session in workdir, newest first."""
    paths = glob.glob(os.path.join(os.path.realpath(workdir), session.SESSION_DIR, "*.jsonl"))
    rows = []
    for path in sorted(paths, key=os.path.getmtime, reverse=True):
        messages, info = session.load(path), meta(path)
        task = next((m["text"] for m in messages if m["role"] == "user"), "")
        rows.append({"session": os.path.splitext(os.path.basename(path))[0],
                     "modified": time.strftime("%Y-%m-%d %H:%M",
                                               time.localtime(os.path.getmtime(path))),
                     "messages": len(messages), "model": info.get("model"),
                     "title": info.get("title") or task[:80], "task": task[:80], "path": path})
    return rows


def search(workdir, query, limit=MAX_RESULTS):
    """Return [{"session", "title", "role", "snippet"}] for messages that contain query."""
    needle, hits = query.lower(), []
    for row in session_rows(workdir):
        for message in session.load(row["path"]):
            text = (message.get("text") or "") + "".join(
                f"\n{c['name']} {json.dumps(c['args'])}" for c in message.get("tool_calls") or [])
            at = text.lower().find(needle)
            if needle and at >= 0:
                snippet = " ".join(text[max(0, at - SNIPPET // 2):][:SNIPPET].split())
                hits.append({"session": row["session"], "title": row["title"],
                             "role": message["role"], "snippet": snippet})
                if len(hits) >= limit:
                    return hits
    return hits


def rename(workdir, session_id, title):
    """Give a session a title in its metadata."""
    path = path_for(workdir, session_id)
    info = {k: v for k, v in meta(path).items() if k != "session"}
    session.write_meta(path, {**info, "title": str(title).strip()[:120]})


def delete(workdir, session_id):
    """Delete a session's transcript and metadata."""
    path = path_for(workdir, session_id)
    stem = os.path.splitext(path)[0]
    os.remove(path)
    if os.path.exists(f"{stem}.meta.json"):
        os.remove(f"{stem}.meta.json")


def feedback(workdir, session_id, turn, rating, note=""):
    """Record a good or bad rating for one turn of a session."""
    if rating not in ("good", "bad"):
        raise ValueError("rating must be good or bad")
    path = os.path.join(workdir, FEEDBACK_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": session_id,
             "turn": turn, "rating": rating, "note": str(note)[:2000]}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def session_search_tool(workdir):
    """Return the session_search tool for workdir."""
    @tool("Search earlier conversations in this project for text: decisions, commands, "
          "errors, file names. Returns matching sessions with snippets.", risk="read",
          query="Text to look for (case-insensitive)")
    def session_search(query):
        hits = search(workdir, query)
        return "\n".join(f"{h['session']} ({h['title'][:50]}) {h['role']}: {h['snippet']}"
                         for h in hits) or f"(no earlier session mentions {query!r})"

    return session_search
