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
| **`bash` sandboxed to the workdir** | **No.** A shell command can read or write anywhere your user account can. |
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
7. Python plugins (planned) are trusted code with the same privileges as ZILL.
   MCP servers (planned) are external processes whose output is untrusted.
8. Run untrusted agents inside an OS-level or container sandbox.
