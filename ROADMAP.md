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

- [ ] **Verify loop:** `"verify": "pytest -q"` in project config. The agent is
      not done until the command passes, up to N attempts.
- [ ] **Checkpoints:** before each state-changing tool, record the changed files
      and their hashes, and snapshot them into a shadow git repo at
      `.zill/checkpoints`. Your own git history is never touched.
      `zill checkpoints` inspects them. `/undo` restores them only when the
      user asks, never automatically.
- [ ] **Todo tool:** a live checklist that is shown in the CLI and survives compaction.
      There is deliberately no separate planner agent.
- [ ] **Hooks:** run commands before or after a tool (for example, a formatter after
      writes). A failing `before` hook blocks the call.

**Acceptance:** a scenario test shows a failing verify command sent back to
the model, and an undo restoring the exact file contents.

### v0.6: Terminal product

The goal is a CLI that is pleasant for people and usable from scripts.

- [ ] **Subcommands:** `zill run`, `resume`, `sessions`, `doctor`, `inspect`,
      `checkpoints`, `fleet`. The existing `zill -p "…"` form keeps working.
- [ ] **`zill doctor`:** checks the Python version, key presence (never the value),
      workdir access, git, session, plugin and MCP config, and permissions, with
      actionable fixes.
- [ ] **`zill inspect`:** shows the model, workdir, policy, tools with their
      source and risk, skills, MCP servers, plugins, connectors and session.
      Never shows secrets.
- [ ] **Config:** `~/.zill/config.json` and `.zill/project.json`, holding the
      default model, mode, verify command, hooks, default skills, and the allowed
      MCP servers, plugins and connectors. ZILL works fine without them.
- [ ] **`--json`:** machine-readable final results, with no decorative output.
- [ ] **Interactive slash commands:** `/help /undo /cost /model /mode /compact /clear /sessions /skills /todo`.
- [ ] Streaming output, plus a token and cost meter.
- [ ] **Profiles:** `coding`, `research`, `writing`, `reviewer`. Built from
      existing pieces, never hardcoded into the loop.

**Acceptance:** `zill --help`, `zill doctor` and `zill inspect` run in CI, and the
`--json` output parses.

### v0.7: Edges (MCP, plugins, connectors)

The goal is to connect ZILL to everything without growing the core.

- [ ] **MCP protocol (`zill/mcp/`):** JSON-RPC primitives in one place. Pin
      one MCP spec revision and document the supported subset: `initialize`,
      `tools/list`, `tools/call`, and shutdown.
- [ ] **MCP client (stdio):** launches the configured server, performs the
      handshake, discovers tools, and wraps them as `mcp__<server>__<tool>`
      Tools behind Policy. If the server crashes, the model gets
      `ERROR: MCP server <name> disconnected: …` and the harness never crashes.
      The server only receives environment variables that were explicitly selected.
- [ ] **`zill mcp add | list | remove`**, with servers stored in `.zill/mcp.json`.
- [ ] **MCP server:** `zill mcp serve --tools read_file,grep,list_files` exposes
      ZILL's tools through the same jail and Policy. It exposes read-only tools by
      default, and a denied call returns a proper MCP error.
- [ ] **Plugins (`zill/plugins/`):** a `manifest.json` (name, version, description,
      entrypoint, permissions), and `register(registry)` receives a restricted
      registry (`add_tool`, `add_skill`) rather than the Harness itself. Plugins
      are discovered in `.zill/plugins/` and `~/.zill/plugins/` but **never run
      until enabled**. Commands: `zill plugin list | enable | disable | inspect`,
      which show requested permissions before enabling.
- [ ] **Connectors:** plugins that also declare credentials (`token_env`), network
      use, and read/write risk. If a credential is missing, the model gets
      `ERROR: <name> connector is not configured`.
- [ ] **Examples:** `examples/plugins/hello_plugin/` (`hello(name)`) and a
      credential-free `examples/connectors/local_notes/` (`list_notes`,
      `read_note`, `create_note`).
- [ ] **`web_fetch` builtin** (network risk), plus an optional `web_search` when a
      Brave or Tavily key is set.

**Acceptance:** a local test MCP server (`add`, `echo`, `failing_tool`) proves
start, handshake, discovery, calls, errors, shutdown, and recovery after
the server dies. Tests also show that disabled plugins never load, that
connectors fail gracefully without credentials, and that policy applies the
same way to every tool source.

### v1.0: Launch

The goal is that a new developer can clone it, read it, trust it, and extend it.

- [ ] **Integration test:** builtins, a skill, a plugin, a connector, a local MCP
      server, memory, a persistent session, safe policy, a sub-agent and
      the audit log in one run. The transcript shows the source of each tool used.
- [ ] **`evals/`:** real-model tasks with mechanical assertions (coding, web
      page, security refusals, memory recall, MCP, plugins, connectors). Run
      manually or on a schedule, not on every PR.
- [ ] **README:** the architecture diagram, the concepts table, and one example
      each of registering a tool, loading a plugin, connecting an MCP server,
      building a connector, and running `zill mcp serve`.
- [ ] **SECURITY.md:** ZILL is not a sandbox; Python plugins are trusted code;
      MCP servers and all tool output are untrusted; approval is
      defense in depth; destructive operations are denied by default; run untrusted
      agents in an OS or container sandbox.
- [ ] Publish `zill-harness` on PyPI with trusted publishing, plus a terminal demo GIF.

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
