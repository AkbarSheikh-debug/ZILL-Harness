"""Day 2 — Security: decide which tool calls may run before any of them do.

Concept: the loop asks before_tool(call) ahead of every execution. A Policy
answers with None (allow) or a reason string (block); the loop hands the
model "BLOCKED: <reason>" as the tool result, so a refusal is something the
model reads and explains, not a crash.

Design rules:
  * Deny patterns come first and no mode overrides them, not even yolo.
    They are a tripwire for catastrophic commands, not a sandbox.
  * Reading is always safe: read tools never touch disk state, and
    core_tools already confines their paths to the working directory.
  * Anything not explicitly approved is refused. The default approver says
    no, and safe mode treats every answer except True as no.
"""

import re

READ_TOOLS = {"read_file", "list_files", "grep"}
MODES = ("read-only", "safe", "yolo")

# Checked with re.search against every bash command.
DENY_PATTERNS = [
    # rm with a recursive flag aimed at /, ~ or $HOME (optionally /*).
    r"\brm\s+(?=(?:-{1,2}[\w-]+\s+)*(?:-[a-zA-Z]*[rR]|--recursive))(?:-{1,2}[\w-]+\s+)+"
    r"[\"']?(?:/|~|\$HOME|\$\{HOME\})/?\*?[\"']?(?=[\s;&|]|$)",
    r"\bsudo\b",
    r"\bmkfs\b",
    r"\bdd\b[^;&|]*\bif=",
    r"\bcurl\b[^;&|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
    r"\bgit\s+push\b[^;&|]*(?:--force\b|\s-f\b)",
    r">\s*/dev/sd[a-z]",
    # The same home-directory wipe in Windows shells, since bash runs through
    # cmd.exe there: any delete command aimed at the profile root.
    r"(?i)\b(?:rmdir|rd|del|erase|Remove-Item|ri)\b[^;&|\n]*"
    r"(?:%USERPROFILE%|\$env:USERPROFILE|\$HOME\b|~(?=[\"'\s;&|]|$)"
    r"|[A-Za-z]:\\Users\\[^\\\s\"';&|]+\\?(?=[\"'\s;&|]|$))",
]
_DENY = [re.compile(p) for p in DENY_PATTERNS]


def _refuse(call, reason):
    """Default approver: nobody is watching, so the answer is no."""
    return False


class Policy:
    """Tool-call gate; pass policy.check as run_loop's before_tool."""

    def __init__(self, mode="safe", approver=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.approver = approver or _refuse

    def check(self, call):
        """Return None to allow call, or a reason string to block it."""
        name = call["name"]
        if name == "bash":
            command = str(call["args"].get("command", ""))
            for pattern in _DENY:
                if pattern.search(command):
                    return ("this command matches a deny pattern for destructive "
                            "operations and is never allowed")
        if name in READ_TOOLS or self.mode == "yolo":
            return None
        if self.mode == "read-only":
            return f"{name} is not allowed in read-only mode"
        reason = f"{name} changes state and needs approval in safe mode"
        if self.approver(call, reason) is True:
            return None
        return f"the user did not approve {name}"
