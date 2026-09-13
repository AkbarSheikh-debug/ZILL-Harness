"""Audit log: why the agent changed something, answerable after the fact.

Concept: the session transcript records what the model said; the audit log
records what the harness decided. Every tool call becomes one JSONL line in
.zill/audit.jsonl: which tool, from which source, at what risk, whether
policy allowed it (and whether a person was asked), how long it took, and
whether it succeeded.

Design rules:
  * Append-only JSONL, one line per call, like sessions.
  * Arguments are redacted before they are written, and results are never
    written at all, only their status. The log is safe to share for debugging.
  * Sub-agents write to the same log as their parent, so a delegated action
    is as visible as a direct one.
"""

import json
import os
import time

from . import credentials

AUDIT_FILE = ".zill/audit.jsonl"


def status_of(result):
    """Classify a tool result string as ok, error or blocked."""
    if result.startswith("BLOCKED:"):
        return "blocked"
    return "error" if result.startswith("ERROR:") else "ok"


def record(workdir, session, call, tool, decision, result, seconds):
    """Append one redacted audit entry for a finished tool call."""
    if decision is None:
        verdict = "unchecked"
    elif not decision.allowed:
        verdict = "denied"
    else:
        verdict = "approved" if decision.needs_approval else "allowed"
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "session": session, "tool": call["name"],
             "source": tool.source if tool is not None else "unknown",
             "risk": decision.risk if decision is not None else None,
             "args": json.loads(credentials.redact(json.dumps(call["args"]))),
             "decision": verdict, "reason": decision.reason if decision else None,
             "seconds": seconds, "status": status_of(result)}
    path = os.path.join(workdir, AUDIT_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
