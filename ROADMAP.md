# ZILL Roadmap

This is the single plan for ZILL: what gets built, in what order, and what
"done" means for each step.

ZILL aims to be a **concise harness product**: a capable, extensible agent
runtime built with as little code as possible. Every item is open for
contribution. Comment on the matching issue, or open one, before starting
large work.

---

## 1. Principles

1. **Keep the core small. Put interoperability at the edges.** MCP, plugins
   and connectors live in edge packages, never inside the loop.
2. **Everything is a Tool.** A builtin function, an MCP tool, a plugin tool
   and a connector operation all look the same to the loop. The model never needs
   to know where a capability comes from.
3. **Policy applies everywhere.** No tool bypasses Policy because it came from
   MCP, a plugin, a connector or a sub-agent.
4. **The LLM is not the architecture.** The architecture is
   *messages + tools + policy + state + adapters*. The model is one component
   that picks what to do next.
5. **Policy is not a sandbox.** Say so plainly in docs, and never advertise more
   isolation than the code delivers.
6. **Ten excellent primitives beat fifty half-working integrations.** If a feature
   makes the architecture harder to explain, simplify it before merging.
7. **Boring, obvious Python.** Every module opens with a docstring naming its
   concept and design rules. Every public function has a docstring. No magic
   metaprogramming, no global mutable registries, and no silent fallbacks
   that hide errors.

## 2. Budgets (enforced in CI)

| Area | Limit |
|---|---|
| Runtime dependencies | **Zero.** Standard library only. |
| Python | 3.10+ |
| Core, `zill/*.py` | **≤ 3,500 lines** |
| Edge packages (`zill/mcp/`, `zill/plugins/`) | **≤ 1,500 lines** |
| Tests, examples, evals | Not counted, but kept readable |

Config files are **JSON**, because `tomllib` needs Python 3.11 and ZILL
supports 3.10.

## 3. Concepts

These plain-English definitions will also appear in the README.

| Concept | What it is |
|---|---|
| **Tool** | One callable operation the model can invoke. |
| **Skill** | Instructions and context loaded on demand. Not executable. |
| **Sub-agent** | An isolated child agent with a clean context. |
| **MCP server** | An external process that exposes tools over the MCP protocol. |
| **Plugin** | A local extension, enabled explicitly, that registers tools. |
| **Connector** | A plugin that integrates an external service and declares the credentials it needs. |
| **Profile** | A named preset: system prompt, allowed tools, skills, policy, model. |

## 4. Target architecture

```
CLI (zill run · resume · sessions · doctor · inspect · mcp · plugin · fleet)
  │
  ▼
Harness ── Memory · Skills · Sessions · Checkpoints · Audit log
  │
  ▼
Agent loop ──► Provider (Gemini · Anthropic · OpenAI-compatible)
  │
  ▼
Policy (risk-aware: allow / ask / deny, dry-run, redaction)
  │
  ▼
Tool registry
  ├── builtin     read_file, write_file, edit_file, bash, list_files, grep, web_fetch, todo
  ├── mcp__*      external MCP servers (stdio)
  ├── plugin__*   local plugins
  └── connector__* plugins with declared credentials

zill mcp serve ──► exposes ZILL's own tools to other MCP clients, through Policy
```

**Tool names use `__` as the namespace separator** (`mcp__github__search`),
because OpenAI and Anthropic reject dots in function names. External tools can
never replace a builtin tool. A name collision is an error at registration, not
a silent override.

---

## 5. Phases

Each phase ships as small, reviewed pull requests. A phase is done only when
its **acceptance checks** pass in CI.

### v0.2: Foundations

The goal is to make ZILL safe to change.

- [x] **Fake provider:** a deterministic script (text → tool call → text) with no network access.
- [x] **Scenario tests** (`unittest`, standard library): normal loop, blocked tool, unknown tool, tool
      exception, path escape, torn session, interrupted tool, compaction,
      memory persistence, skill loading, sub-agent depth limit, approval
      accept and deny, fleet ordering, provider retries.
- [x] **Provider contract:** documented neutral message format, tool-call `id`s
      (old sessions upgraded on load), and optional model metadata
      (`supports_tools`, `supports_parallel_tools`, `context_window`,
      `max_output_tokens`). The Gemini thought-signature handling stays intact.
- [x] **Session metadata:** a `<session>.meta.json` beside each transcript with the
      session id, start time, model, workdir, ZILL version, mode, tools and skills. No secrets.
- [x] **Windows shell awareness:** tell the model which shell `bash` really runs.
- [x] **CI:** tests on Linux, macOS and Windows, plus the dependency and line-budget checks.

**Acceptance:** `python -m unittest discover` passes with no API key.

### v0.3: Providers and keys

The goal is to let users bring any API key.

- [x] `zill/providers/` with `gemini`, `anthropic` and `openai_compat` adapters.
- [x] **Prefixed model names:** `gemini:…`, `anthropic:…`, `openai:…`, `openrouter:…`,
      `groq:…`, `deepseek:…`, `ollama:…`, `lmstudio:…`, with presets for base URLs and key variables.
- [x] **`zill setup`:** paste keys once, and they are stored in `~/.zill/credentials.json`
      (file mode `0600` on Unix). Environment variables always take priority.
      An interactive start with no key runs setup automatically.
- [x] **Redaction utility:** every configured secret value is replaced with `[REDACTED]`
      in CLI output and tool-argument display. Audit logs will use it in v0.4.
- [x] Anthropic prompt caching for the system prompt and message history, with
      thinking blocks replayed verbatim.
- [x] **Quota-aware retries:** honor server-requested delays, and never retry a daily-quota 429.

**Acceptance:** the same scenario suite passes against a fake adapter for each
wire format, and a test proves that a secret never appears in printed output.

### v0.4: Safety core

The goal is policy that you can trust and inspect.

- [x] **Capability metadata:** `Tool` gains two defaulted fields, `source`
      (`builtin | mcp | plugin | connector`) and `risk`
      (`read | write | execute | network | destructive | credentialed`).
      Undeclared tools default to `execute`.
- [x] **Risk-aware Policy:** `Policy.decide()` returns a `Decision` with `allowed`, `reason`,
      `needs_approval` and `risk`. Reads are allowed; writes, execution and network need
      approval in safe mode; destructive operations are denied in every mode.
      `Policy.check()` still works.
- [x] **Command classification:** `bash` commands are classified by what they do.
      `git push`, `pull`, `fetch` and `clone`, `curl`, `wget` and package installs are network;
      `git push --force` and the deny patterns are destructive. `git commit` stays
      `execute`, because a shell line can always do more than commit.
- [x] **`--dry-run`:** reads only. It binds sub-agents through the shared policy, and future
      MCP, plugin and connector tools pass through the same `decide()`.
- [x] **Audit log** `.zill/audit.jsonl`: time, session (sub-agents are labeled), tool,
      source, risk, redacted arguments, decision, reason, duration and status.
      Results are never written.
- [x] **Prompt-injection resistance:** system rules state that tool output,
      files and external content are untrusted data. A test feeds in hostile tool
      output and checks that the destructive and exfiltration calls that follow are
      blocked and never execute. We do not claim this is fully solved.
- [x] **Timeouts:** `bash` has a per-call timeout, model HTTP calls time out after 600s,
      and Ctrl-C leaves a resumable session. MCP and plugin timeouts come with v0.7.
- [x] **More events:** `session_start/end`, `provider_start/end`, `tool_blocked`,
      `compaction` and `error`. Events are plain dicts, and consumers can ignore any of them.

**Acceptance:** dry-run blocks every write path, the audit log contains no secrets,
and the hostile-output test passes.

### v0.5: Reliable builds

The goal is that what ZILL builds actually works.

- [x] **Verify loop:** `"verify": "pytest -q"` in `.zill/project.json`, or `--verify`.
      The run is not done until the command passes, with up to 3 fix rounds. The command goes
      through Policy like a bash call, so dry-run and approvals apply and nothing runs
      without permission.
- [x] **Checkpoints:** before each allowed state-changing tool, the work tree is snapshotted
      into a shadow git repo at `.zill/checkpoints`, with git storing file hashes. Your own
      git history is never touched. `zill checkpoints` lists them; `zill undo [id]` and
      `/undo` restore them only when you ask. Every restore is itself snapshotted.
      It needs git on PATH; without git, checkpoints are off (no file-copy fallback).
- [x] **Todo tool:** a live checklist that is shown in the CLI, survives compaction and
      `--resume`, and can be viewed with `/todo`. There is deliberately no planner agent.
- [x] **Hooks:** commands in `.zill/project.json` run before or after matching
      state-changing tools (for example, a formatter after writes). A failing `before` hook
      blocks the call, and a failing `after` hook's output goes to the model. `{path}` is
      only substituted for plain paths, and every hook goes through Policy.

**Acceptance:** a scenario test shows a failing verify command sent back to
the model, and an undo restoring the exact file contents.

### v0.6: Terminal product

The goal is a CLI that is pleasant for people and usable from scripts.

- [x] **Subcommands:** `zill run`, `resume`, `setup`, `sessions`, `doctor`, `inspect`,
      `checkpoints`, `undo`, `fleet`. `zill -p "…"` and `zill "task"` keep working.
- [x] **`zill doctor`:** checks the Python version, git, workdir access, config
      validity, key presence (never the value), the chosen model's key, and
      credentials-file permissions, each with a fix. MCP and plugin checks arrive with v0.7.
- [x] **`zill inspect`:** shows the model, provider, key status, workdir, mode, profile,
      verify, hooks, checkpoints, memory, tools with their source and risk, skills and
      latest session, without calling a model or writing a file. Never shows secrets.
- [x] **Config:** `~/.zill/config.json` (model, mode, profile, prices) and
      `.zill/project.json` (model, mode, profile, verify, hooks), with unknown keys
      rejected. A project file may only make the mode stricter. Allowed MCP servers and
      plugins arrive with v0.7.
- [x] **`--json`:** one result object (result, usage, cost, session, todo) or one error
      object, with no decorative output.
- [x] **Interactive slash commands:** `/help /cost /model /mode /compact /clear /todo /undo
      /checkpoints /sessions /skills /exit`.
- [x] **Streaming and cost:** output streams on a terminal for all three adapters,
      printed a line at a time so redaction still works. A token and cost meter covers
      `/cost`, the headless summary and `--json`. Built-in prices cover documented Anthropic
      models only; others can be added under `prices`.
- [x] **Profiles:** `coding`, `research`, `writing`, `reviewer`. Each is a prompt, a tool
      allowlist and a strictest mode, never hardcoded into the loop.

**Acceptance:** `zill --help`, `zill doctor` and `zill inspect` run in CI, and the
`--json` output parses.

### v0.7: Edges (MCP, plugins, connectors)

The goal is to connect ZILL to everything without growing the core.

- [x] **MCP protocol (`zill/mcp/protocol.py`):** JSON-RPC primitives in one place, pinned to
      spec revision `2025-06-18` (and `2025-03-26` and `2024-11-05` servers accepted). The
      supported subset is `initialize`, `notifications/initialized`, `ping`, `tools/list`
      with pagination, and `tools/call`.
- [x] **MCP client (stdio):** launches the server, performs the handshake, answers server
      pings, discovers tools, and wraps them as `mcp__<server>__<tool>` Tools behind Policy.
      Schemas are reduced to the subset every provider accepts. A crash gives the model
      `ERROR: MCP server <name> disconnected …`, and the next call restarts the server.
      Every request has a timeout. The server receives only basic environment variables
      plus the ones configured (`${VAR}` resolved), and its stderr goes to `.zill/mcp/<name>.log`.
- [x] **`zill mcp add | trust | list [--tools] | remove`**, with servers stored in
      `.zill/mcp.json`. **Servers only start once approved:** approvals live in
      `~/.zill/trust.json` and pin a fingerprint of the entry, so a cloned repo's config never
      runs by itself and any edit needs re-approval.
- [x] **MCP server:** `zill mcp serve --tools read_file,grep,list_files` exposes ZILL's tools
      through the same jail, Policy and audit log. It exposes read-only tools and uses
      read-only mode by default. A denied call is an `isError` result, and an unknown tool is
      a JSON-RPC error.
- [x] **Plugins (`zill/plugins/`):** `manifest.json` (name, version, description, entrypoint,
      kind, permissions, credentials), and `register(registry)` receives a restricted registry
      (`add_tool`, `credential`, `workdir`). A tool's risk must be a declared permission.
      Plugins are discovered in `.zill/plugins/` and `~/.zill/plugins/` and **never imported
      until enabled**. The enablement pins a fingerprint of every file, so an edit disables the
      plugin. Commands: `zill plugin list | inspect | enable | disable`. `add_skill` is deferred;
      skills stay folders.
- [x] **Connectors:** plugins with `"kind": "connector"` that declare `credentials`. If one is
      missing, the tools answer `ERROR: <name> connector is not configured: set …`.
- [x] **Examples:** `examples/plugins/hello_plugin/` and the credential-free
      `examples/connectors/local_notes/`.
- [x] **Web tools:** a `web_fetch` builtin (network risk, HTML to text, size-bounded, http or
      https only), plus `web_search` when a Brave or Tavily key is set.

**Acceptance:** a local test MCP server (`add`, `echo`, `failing_tool`) proves
start, handshake, discovery, calls, errors, shutdown, and recovery after
the server dies. Tests also show that disabled plugins never load, that
connectors fail gracefully without credentials, and that policy applies the
same way to every tool source.

### v1.0: Launch

The goal is that a new developer can clone it, read it, trust it, and extend it.

- [x] **Integration test** (`tests/test_integration.py`): builtins, a skill, a plugin, a
      connector, a local MCP server, memory, a persistent session, safe policy with
      approvals, a sub-agent, checkpoints and the audit log in one run. The audit log shows
      the source, risk and decision of each tool used.
- [x] **`evals/`:** 16 real-model tasks with mechanical checks (coding, web page, security
      refusals, memory recall, MCP, plugins, connectors). They run manually or from the
      `Evals` workflow, never on every PR.
- [x] **README:** the architecture diagram, how a call flows from model to tool to
      audit, the concepts table, and examples of registering a tool, loading a plugin,
      building a connector, connecting an MCP server, and running `zill mcp serve`.
- [x] **SECURITY.md:** ZILL is not a sandbox; Python plugins are trusted code; MCP servers
      and all tool output are untrusted; approval is defense in depth; destructive operations
      are denied; projects get no automatic trust; run untrusted agents in a sandbox.
- [x] **Release pipeline:** `release.yml` publishes `zill-harness` to PyPI with trusted
      publishing when a GitHub release is published. The one-time pypi.org publisher setup
      is the maintainer's step.
- [ ] Terminal demo GIF (needs a real recorded session).
- [x] **Release checks in CI:** standard-library-only core, no committed secrets, and a
      consistent version and changelog.

**Final check:**
```sh
python -m compileall zill
python -m unittest discover
zill --help && zill doctor && zill inspect
```
It also confirms that the core has no third-party imports, no secrets are in
the repo or logs, and all acceptance checks above still pass.

---

## 6. Later (after v1.0, only if the design stays simple)

- MCP Streamable HTTP transport and an optional adapter for the official MCP SDK (a `zill-harness[mcp-sdk]` extra)
- Parallel tool execution (`parallel_tools=True`, opt-in per tool, results kept in order)
- Read-only resources (`project://README`, `memory://project`)
- Structured tool results and artifact tracking
- Subprocess isolation mode for plugins
- Container sandbox mode

## 7. Not planned

A web UI, vector memory or RAG, a plugin marketplace or install-from-URL, a
mandatory planner agent, or a workflow engine. These would make ZILL harder to
explain without making it more useful.
