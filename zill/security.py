"""Day 2 — Security: decide which tool calls may run before any of them do.

Concept: the loop asks before every execution. A Policy classifies the call
by risk, from the tool's declared risk, refined for bash by what the command
does, and returns a Decision: allowed or not, why, whether a person was
asked, and the risk. The loop hands the model "BLOCKED: <reason>" as the
tool result, so a refusal is something the model reads and explains.

Design rules:
  * Risk levels: read, write, execute, network, destructive, credentialed.
    Destructive calls are denied in every mode, even yolo. The deny patterns
    are a tripwire for catastrophic commands, not a sandbox.
  * Reads are always allowed: read tools never change state, and
    core_tools already confines their paths to the working directory.
  * Dry-run and read-only allow reads only. Safe mode asks the approver for
    everything else, and only an explicit True counts as yes.
  * The modes between safe and yolo loosen one step at a time: edits runs
    builtin write tools (jailed to the working directory, checkpointed) without
    asking; auto also hands every other state-changing call to the reviewer,
    runs it when the reviewer has no concern, and asks the approver otherwise.
    Tools from MCP servers and plugins are never auto-approved as edits.
  * The policy object is shared with sub-agents, so every rule, dry-run
    included, applies to them too. Project hook and verify commands are
    decided like bash calls, so config files get no special trust.
"""

import re
from dataclasses import dataclass

READ_TOOLS = {"read_file", "list_files", "grep"}  # for callers that pass no Tool
COMMAND_TOOLS = {"bash", "hook", "verify", "terminal_send"}  # "command" is a shell line
MODES = ("read-only", "safe", "edits", "auto", "yolo")  # strictest first
RISKS = ("read", "write", "execute", "network", "destructive", "credentialed")

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
# Commands that reach the network: fetches, pushes, pulls, package installs.
NETWORK = re.compile(
    r"(?i)\b(?:curl|wget|ssh|scp|rsync|ftp|nc|Invoke-WebRequest|iwr|Invoke-RestMethod)\b"
    r"|\bgit\s+(?:push|pull|fetch|clone)\b"
    r"|\b(?:pip3?|npm|pnpm|yarn|uv|cargo|gem)\s+(?:install|add|publish)\b")
DENIED = "this command matches a deny pattern for destructive operations and is never allowed"


@dataclass
class Decision:
    """The outcome of one policy check."""

    allowed: bool
    reason: str | None
    risk: str
    needs_approval: bool = False


def _refuse(call, reason):
    """Default approver: nobody is watching, so the answer is no."""
    return False


def classify(call, tool=None):
    """Return the risk of call: the tool's declared risk, refined for shell commands."""
    name = call["name"]
    risk = tool.risk if tool is not None else ("read" if name in READ_TOOLS else "execute")
    if name in COMMAND_TOOLS:
        command = str(call["args"].get("command", ""))
        if any(pattern.search(command) for pattern in _DENY):
            return "destructive"
        if NETWORK.search(command):
            return "network"
    return risk


class Policy:
    """Tool-call gate; Harness passes every call through decide()."""

    def __init__(self, mode="safe", approver=None, dry_run=False, plan=False, reviewer=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.approver = approver or _refuse
        self.dry_run = dry_run
        self.plan = plan  # plan mode: reads only, until the user approves a plan
        # auto mode: reviewer(call, risk) returns None when a call looks safe, else a concern
        self.reviewer = reviewer

    def decide(self, call, tool=None, ask_reason=None):
        """Classify call and return a Decision; outside yolo this may ask the approver.

        ask_reason makes a call that is not a read need approval in every mode,
        yolo included, and is what the approver and a refusal say.
        """
        name, risk = call["name"], classify(call, tool)
        if risk == "destructive":
            return Decision(False, DENIED, risk)
        if risk == "read":
            return Decision(True, None, risk)
        if self.plan:
            return Decision(False, f"plan mode: {name} ({risk}) waits until the user approves "
                                   f"a plan; call exit_plan_mode with your plan", risk)
        if self.mode == "yolo" and not self.dry_run and not ask_reason:
            return Decision(True, None, risk)
        if self.dry_run:
            return Decision(False, f"dry-run mode: {name} ({risk}) was not run", risk)
        if self.mode == "read-only":
            return Decision(False, f"{name} is not allowed in read-only mode", risk)
        concern = ask_reason
        if not concern and self.mode in ("edits", "auto"):
            if risk == "write" and getattr(tool, "source", None) == "builtin":
                return Decision(True, None, risk)
            if self.mode == "auto" and self.reviewer is not None:
                concern = self.reviewer(call, risk)
                if not concern:
                    return Decision(True, None, risk)
                concern = f"the auto-mode safety check paused it: {concern}"
        reason = concern or f"{name} ({risk}) changes state and needs approval in {self.mode} mode"
        if self.approver(call, reason) is True:
            return Decision(True, None, risk, needs_approval=True)
        refusal = f"the user did not approve {name}" + (f": {concern}" if concern else "")
        return Decision(False, refusal, risk, needs_approval=True)

    def check(self, call, tool=None):
        """Return None to allow call, or a reason string to block it."""
        decision = self.decide(call, tool)
        return None if decision.allowed else decision.reason
