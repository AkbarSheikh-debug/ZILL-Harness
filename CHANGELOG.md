# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Offline test suite (`python -m unittest discover`) with a scripted
  `FakeProvider`, covering the loop, tools, policy, sessions, compaction, memory,
  skills, sub-agents, fleet and provider retries. No API key needed. It runs in CI.
- Tool-call ids: every call carries an `id`, and its result repeats it.
  Sessions saved before ids existed are upgraded when loaded.
- `provider.model_info()` declares model capabilities. The default compaction
  budget is now 60% of the model's context window.
- `<session>.meta.json` sidecar with the model, version, mode, tools and skills. It never contains secrets.
- `zill.__version__`, which is also the package version source.

- **Providers:** Anthropic Claude and OpenAI-compatible adapters (OpenAI,
  OpenRouter, Groq, DeepSeek, Ollama, LM Studio), selected with
  `-m provider:model`. Bare `claude-…`, `gpt-…` and `o3…` names are recognized.
- **`zill setup`:** paste keys with hidden input, saved to `~/.zill/credentials.json`
  (mode 0600). Starting `zill` with no key runs it automatically.
- **Automatic model choice:** with no model set, ZILL uses the default of
  the first provider that has a key.
- **Redaction:** known secret values are masked in all terminal output.
- **Anthropic prompt caching:** the system prompt and history are cached, and
  thinking blocks are replayed verbatim.
- **Gemini call ids:** ids issued by Gemini are echoed on the function call and its response.

- **Risk and source on tools:** `@tool(..., risk=, source=)`. Core tools declare `read`,
  `write` or `execute`, and `bash` commands are classified further as `network` or
  `destructive`.
- **Policy decisions:** `Policy.decide()` returns a `Decision` (allowed, reason, risk,
  needs_approval). Destructive calls are denied even in yolo mode.
- **`--dry-run`:** only reads run, for the agent and its sub-agents.
- **Audit log:** `.zill/audit.jsonl` records every tool call's risk, decision, duration and
  status, with arguments redacted.
- **Events:** `session_start`, `session_end`, `provider_start`, `provider_end`,
  `tool_blocked`, `compaction`, `error`. `tool_end` now carries `id` and `seconds`.
- **Untrusted-data rules:** the system prompt treats tool output and files as data, never
  instructions, and forbids revealing secrets.

- **Verify loop:** `--verify "pytest -q"` or `"verify"` in `.zill/project.json`. A failing
  check goes back to the model for up to 3 fix rounds before the run finishes.
- **Checkpoints:** a shadow git repository at `.zill/checkpoints` snapshots the work tree before
  every allowed state-changing call. `zill checkpoints`, `zill undo [id]`, and interactive
  `/undo` and `/checkpoints` restore them, and each restore can be restored.
- **Todo tool:** a checklist the model keeps, printed in the CLI, re-injected after
  compaction, rebuilt on `--resume`, and shown by `/todo`.
- **Hooks:** `"hooks"` in `.zill/project.json` run commands before or after matching
  tools. A failing before-hook blocks the call; a failing after-hook reports to the model.
- **Command policy:** hook and verify commands pass through Policy (approval, dry-run, deny
  patterns) and are written to the audit log.
- **`after_tool` socket:** `run_loop` gains `after_tool(call, result)`.

### Changed
- **Sub-agent approval:** `spawn_agent` is read-risk. The child's own calls are still gated
  by the shared policy, so safe mode no longer asks twice.
- **Session metadata:** `.meta.json` lists each tool's source and risk, and records dry-run.
- The system prompt names the real shell the `bash` tool runs (`COMSPEC` on
  Windows) and asks for Windows command syntax there.
- **Provider package:** the Gemini wire code moved to `zill/providers/gemini.py`, and
  `zill/provider.py` is now the dispatcher.
- **Key lookup order:** the provider-specific variable (for example `GEMINI_API_KEY`) is read
  before `ZILL_API_KEY`.
- **Retries:** they wait at least as long as the server asks (`Retry-After` or
  `retryDelay`), and a daily-quota 429 fails immediately instead of retrying.
- **Line budget:** CI counts every module under `zill/` against the core budget (edge
  packages separately).

### Fixed
- `zill` no longer crashes when stdout is not a real console stream.

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
