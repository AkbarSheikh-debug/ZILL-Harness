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

- **Subcommands:** `zill run "task"`, `zill resume`, `zill doctor`, `zill inspect`,
  `zill sessions`, `zill fleet jobs.json`. `zill -p` still works.
- **`--json`:** headless runs print one object with the result, model, session, usage,
  cost and todo; errors are JSON too.
- **`--profile`:** `coding`, `reviewer`, `research` or `writing` narrows the prompt, tools
  and mode.
- **Config:** `~/.zill/config.json` sets defaults (model, mode, profile, prices), and
  `.zill/project.json` gains `model`, `mode` and `profile`. Unknown keys are errors, and a
  project may only tighten the mode.
- **Streaming:** Gemini, Anthropic and OpenAI-compatible adapters stream on a terminal,
  and text is printed a line at a time so secret redaction stays exact.
- **Cost meter:** usage totals per harness, `/cost`, a summary after headless runs, and
  `cost_usd` in `--json`, from documented prices only.
- **Slash commands:** `/help /cost /model /mode /compact /clear /sessions /skills /exit`
  join `/todo /undo /checkpoints`.
- **CI:** it runs `zill inspect --json` and `zill doctor --json` and checks that the output parses.

- **MCP client:** servers in `.zill/mcp.json` run over stdio (spec 2025-06-18: initialize,
  ping, tools/list with pagination, tools/call) and appear as `mcp__<server>__<tool>`, with
  timeouts, crash recovery, an explicit environment and a per-server stderr log.
- **MCP server:** `zill mcp serve` offers ZILL's tools to other MCP clients, through
  Policy and the audit log, read-only by default.
- **`zill mcp` commands:** `add | trust | list | remove | serve`.
- **Plugins and connectors:** manifests with declared permissions and credentials, a
  restricted registry, `zill plugin list | inspect | enable | disable`, and namespaced tools
  (`plugin__…`, `connector__…`).
- **Trust:** `~/.zill/trust.json` approves MCP servers and plugins by fingerprint, so
  nothing shipped inside a project runs until the user approves it, and edits need
  re-approval.
- **Web tools:** `web_fetch` (network risk), and `web_search` with a Brave or Tavily key.
- **Examples:** `examples/plugins/hello_plugin` and `examples/connectors/local_notes`.
- **Doctor and inspect:** `zill doctor` reports unapproved MCP servers and changed plugins,
  and `zill inspect` lists extension tools and their notes.

### Changed
- **`cli.py` split:** subcommands moved from `cli.py` to `commands.py`; run settings are
  resolved in `settings.py`.
- **System prompt separator:** the skills catalog and extra prompt text are now separated
  by a blank line (they were run together).
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
