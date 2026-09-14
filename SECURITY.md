# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report them privately through GitHub's
[private vulnerability reporting](https://github.com/AkbarSheikh-debug/ZILL-Harness/security/advisories/new).
Include the steps to reproduce, the impact, and the affected version or commit.
You should get a response within 7 days.

## Supported versions

Only the latest release on `main` receives security fixes.

## What ZILL does and does not protect against

ZILL runs a language model that can execute tools on your machine. Know
where its safety boundary is:

| Protection | Status |
|---|---|
| File tools (`read_file`, `write_file`, `edit_file`, `list_files`, `grep`) confined to the workdir, with symlinks resolved | Yes |
| Deny patterns for catastrophic shell commands (`sudo`, `rm -rf /`, `mkfs`, `curl \| sh`, force push, and more) | Yes, but only as a tripwire |
| `safe` mode: every state-changing call needs your approval | Yes |
| `read-only` mode and `--dry-run`: no state-changing calls at all, sub-agents included | Yes |
| Risk classification per call (`read`, `write`, `execute`, `network`, `destructive`), with destructive denied in every mode | Yes, but heuristic for `bash` |
| Audit log of every tool call and policy decision, secrets redacted (`.zill/audit.jsonl`) | Yes |
| Commands from `.zill/project.json` (verify, hooks) decided by the same policy as `bash`, never trusted automatically | Yes |
| A project file can make the mode stricter but never looser (a cloned repo cannot switch you to `yolo`) | Yes |
| Keys never printed by `doctor`, `inspect`, `--json`, or streamed output (redacted a line at a time) | Yes, for known key values |
| Checkpoints before every change, restorable with `zill undo` | Yes, if git is installed. Only changes inside the workdir are captured, and ignored files are not. |
| **`bash` sandboxed to the workdir** | **No.** A shell command can read or write anywhere your user account can. |
| After web, network-command, MCP or connector output enters a task (sub-agents included), saving to `ZILL.md` through `remember`, `write_file` or `edit_file` needs your approval in every mode, `yolo` too, and is refused when no one can approve. The gate lifts at your next task. | Yes, but a `bash` command that edits `ZILL.md`, or a local file the agent reads, is not caught |
| Protection from prompt injection in files or web content the agent reads | No |

**Recommendations**

- Use `safe` mode (the interactive default) on any machine or repo you care about.
- Only run `yolo` mode (the default with `-p`) in a disposable directory,
  container, or VM.
- Keep API keys in environment variables or `zill setup`, which stores them in
  `~/.zill/credentials.json` with owner-only permissions. Never commit them or
  paste them into prompts. ZILL masks known key values in terminal output, but
  a tool can still write a key into a file if the model is told to. Session transcripts in `.zill/sessions` can contain anything the
  agent saw, so treat them as sensitive.

## Principles every contribution keeps

1. ZILL is not a sandbox. The workdir jail stops path traversal in file tools;
   it does not sandbox the operating system.
2. Approval policies are defense in depth, not a security boundary.
3. Tool descriptions, tool results, files and external content are untrusted
   data, never instructions.
4. Destructive operations are denied by default.
5. Secrets never go into prompts or tool descriptions, and are never printed or logged.
6. Every tool, whatever its source, goes through Policy.
7. Python plugins are trusted code with the same privileges as ZILL. Enabling
   one is a trust decision, not a sandbox. MCP servers are external processes,
   and their tool descriptions, annotations and output are untrusted data; an
   annotation never lowers a tool's risk.
8. Nothing shipped inside a project (`.zill/mcp.json`, `.zill/plugins/`) runs
   until the user approves it. Approvals live in `~/.zill/trust.json`, pinned to
   a fingerprint, so a changed command or file needs approval again.
9. Run untrusted agents inside an OS-level or container sandbox.
