# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security
- Memory writes after untrusted content: once a network-risk call (`web_fetch`,
  `web_search`, `curl` and the like) or an MCP or connector tool has run in a task, or
  in one of its sub-agents, `remember` and file writes to `ZILL.md` need explicit
  approval in every mode, including `yolo`. With no one to ask they are blocked. The
  gate lifts when the user gives the next task.

### Added
- Loop guard: when the same tool call returns the same result 3 times within the
  last 12 calls, the model is warned; at 5 the run stops with a tool-less wrap-up
  and a `loop_detected` event.

## [1.0.0] - 2026-09-14

The first stable release: a concise, dependency-free coding-agent harness that
works with Gemini, Claude, OpenAI-compatible models and local models, and that
connects to MCP servers, plugins and connectors under a single policy.

### Added

**Providers and keys**
- Anthropic Claude and OpenAI-compatible adapters (OpenAI, OpenRouter, Groq,
  DeepSeek, Ollama, LM Studio) alongside Gemini, selected with `-m provider:model`.
  Bare `claude-`, `gpt-` and `o3` names are recognized.
- `zill setup` pastes keys with hidden input into `~/.zill/credentials.json` (mode 0600),
  and runs automatically when `zill` starts without a key.
- The default model follows the first provider that has a key.
- Streaming output for all three adapters, printed a line at a time so secret
  redaction stays exact.
- Anthropic prompt caching; thinking blocks are replayed verbatim. Gemini-issued call
  ids are echoed back.
- Retries honor `Retry-After` and `retryDelay`, and a daily-quota 429 fails
  immediately instead of spending more quota.

**Safety**
- Every tool declares a `source` and a `risk` (`read`, `write`, `execute`, `network`,
  `destructive`, `credentialed`). `bash` commands are classified by what they do.
- `Policy.decide()` returns a `Decision`, and destructive calls are denied in every mode.
- `--dry-run` allows only reads, for the agent and its sub-agents.
- `.zill/audit.jsonl` records every tool call's source, risk, decision, duration and
  status, with arguments redacted.
- Known secret values are redacted from all terminal output.
- System-prompt rules treat tool output, files and web content as untrusted data;
  a test proves that hostile tool output cannot trigger destructive or exfiltrating calls.
- `~/.zill/trust.json`: nothing shipped inside a project (MCP servers, plugins) runs
  until the user approves it, pinned to a fingerprint so edits need re-approval.
- A project file may only make the mode stricter, never looser.

**Reliable builds**
- Verify loop: `--verify "pytest -q"` or `"verify"` in `.zill/project.json`; a failing
  check goes back to the model for up to 3 fix rounds.
- Checkpoints in a shadow git repository before every change, with
  `zill checkpoints`, `zill undo [id]`, `/undo` and `/checkpoints`. The user's own `.git`
  is never touched.
- A todo tool that survives compaction and `--resume`.
- Before and after hooks around state-changing tools, decided by Policy like `bash`.

**Terminal product**
- Subcommands: `zill run`, `resume`, `setup`, `doctor`, `inspect`, `sessions`,
  `checkpoints`, `undo`, `fleet`, `mcp`, `plugin`. `zill -p` still works.
- `--json` for scripts, `--profile` (`coding`, `reviewer`, `research`, `writing`), and
  `~/.zill/config.json` plus `.zill/project.json` with strict validation.
- A cost meter from documented prices (`/cost`, headless summary, `cost_usd`).
- Slash commands: `/help /cost /model /mode /compact /clear /todo /undo /checkpoints
  /sessions /skills /exit`.

**Extensions**
- MCP client over stdio (spec 2025-06-18) with timeouts, crash restart and an explicit
  environment; tools appear as `mcp__<server>__<tool>`. `zill mcp add | trust | list |
  remove`.
- `zill mcp serve` offers ZILL's tools to other MCP clients through Policy and the audit
  log, read-only by default.
- Plugins and connectors with manifests, declared permissions and credentials, a restricted
  registry, and `zill plugin list | inspect | enable | disable`.
- `web_fetch`, plus `web_search` with a Brave or Tavily key.
- Examples: `examples/plugins/hello_plugin` and `examples/connectors/local_notes`.

**Quality**
- An offline test suite with a scripted `FakeProvider`, an independent MCP test server,
  a full integration test, and release checks (standard library only, no committed
  secrets, version consistency). It runs on Linux, macOS and Windows with Python 3.10
  and 3.13.
- `evals/`: real-model tasks with mechanical checks, run manually or from GitHub
  Actions.
- A release workflow that publishes `zill-harness` to PyPI with trusted publishing.

### Changed
- `zill/provider.py` is now a dispatcher; wire formats live in `zill/providers/`.
- Tool calls carry ids and results repeat them. Older sessions are upgraded on load.
- The compaction budget defaults to 60% of the model's context window.
- `spawn_agent` is read-risk; the child's own calls are still gated, so safe mode no
  longer asks twice.
- Subcommands live in `commands.py`; run settings are resolved once in `settings.py`.
- The default Gemini model is `gemini-3.6-flash`, which free-tier keys can use
  (`gemini-3.1-pro-preview` has no free-tier quota).

### Fixed
- `zill` no longer crashes when stdout is not a real console stream.
- The skills catalog and extra system-prompt text are separated by a blank line.
- A second `undo` no longer jumps forward in checkpoint history.

## [0.1.0] - 2026-09-13

### Added
- First public release.
- Agent loop, workdir-confined file tools and `bash`, and a permission policy
  (`read-only`, `safe`, `yolo`) with deny patterns.
- Context compaction, `ZILL.md` project memory, on-demand skills.
- Crash-safe JSONL sessions with `--resume`, sub-agents, and `run_fleet`.
- `zill` command-line entry point, installable with pip.
- Gemini provider.
- MIT license, contributing guide, code of conduct, security policy, and CI.
