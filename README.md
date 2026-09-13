# ZILL Harness

**ZILL** is a concise coding-agent harness product: a full terminal coding agent
built with as little code as possible. It has ten core modules and zero
dependencies. It uses only the Python standard library and makes one HTTPS call
per model turn.

It has the same parts as the large harnesses: an agent loop, workdir-confined
tools, a permission policy, context compaction, project memory, on-demand
skills, crash-safe sessions, sub-agents, and parallel runs. Each part lives in
its own file, and each file opens with the concept it implements and the
design rules it keeps.

[![CI](https://github.com/AkbarSheikh-debug/ZILL-Harness/actions/workflows/ci.yml/badge.svg)](https://github.com/AkbarSheikh-debug/ZILL-Harness/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Install

One command. It needs Python 3.10 or newer:

```sh
pip install git+https://github.com/AkbarSheikh-debug/ZILL-Harness.git
```

Then start it. The first time, ZILL asks you to paste an API key:

```sh
zill            # no key yet? it runs `zill setup` for you
zill setup      # add or change keys any time (input is hidden)
```

Keys are saved to `~/.zill/credentials.json`, readable only by you.
Environment variables work too and always take priority
(`export ANTHROPIC_API_KEY=...`, or in PowerShell `$env:ANTHROPIC_API_KEY = "..."`).

### Providers

Pick a model with `-m provider:model`. With no `-m`, ZILL uses `ZILL_MODEL`,
or else the default model of the first provider you have a key for.

| Provider | Example `-m` | Key variable |
|---|---|---|
| Google Gemini | `gemini:gemini-3.1-pro-preview` (default) | `GEMINI_API_KEY` |
| Anthropic Claude | `anthropic:claude-opus-5` (default) | `ANTHROPIC_API_KEY` |
| OpenAI | `openai:gpt-5` (default) | `OPENAI_API_KEY` |
| OpenRouter | `openrouter:<vendor>/<model>` | `OPENROUTER_API_KEY` |
| Groq | `groq:<model>` | `GROQ_API_KEY` |
| DeepSeek | `deepseek:deepseek-chat` (default) | `DEEPSEEK_API_KEY` |
| Ollama (local) | `ollama:qwen3` | none |
| LM Studio (local) | `lmstudio:<model>` | none |

Bare names work as well: `claude-…` goes to Anthropic, `gpt-…` and `o3…` go to
OpenAI, and anything else goes to Gemini. Set `ZILL_BASE_URL` to point the
chosen provider at another endpoint, such as a proxy or a self-hosted
OpenAI-compatible server. `ZILL_API_KEY` is a fallback key for any provider.

## Use it

```sh
zill                                          # interactive: prompt loop, safe mode
zill -p "add a --json flag to cli.py" -d ./myproject   # headless, yolo mode
zill --resume -d ./myproject                  # continue the latest session there
```

| Flag | Meaning |
|---|---|
| `-p, --prompt` | Run one task headlessly and exit. |
| `-d, --workdir` | Directory the agent works in (default `.`). |
| `-m, --model` | `provider:model` (see Providers). |
| `--mode` | `safe`, `yolo` or `read-only`. Default: `safe` interactively, `yolo` with `-p`. |
| `--dry-run` | Plan and inspect only: every call that is not a read is blocked, sub-agents included. |
| `--verify` | A run is done only when this command passes, for example `"pytest -q"`. |
| `--resume` | Load the newest session in the workdir before running. |
| `--max-turns` | Tool turns allowed per task (default 120). |

In safe mode, every call that changes state asks
`approve bash({"command": ...})? [y/N]`. Ctrl-C stops the current run without
losing the session. Ctrl-D exits.

### Make it prove its work

Put the project's check in `.zill/project.json`, and a run isn't done until it
passes. Failures go back to the agent, with up to 3 fix rounds:

```json
{
  "verify": "python -m pytest -q",
  "hooks": [
    {"when": "after", "tool": "write_file", "match": "*.py", "run": "ruff format {path}"},
    {"when": "before", "match": "generated/*", "run": "python -c \"raise SystemExit('do not edit generated files')\""}
  ]
}
```

A failing `before` hook blocks the call, and a failing `after` hook's output
goes back to the agent. Verify and hook commands go through the same policy as
`bash`, so safe mode asks first and `--dry-run` skips them.

### Take it back

Before every change, ZILL snapshots the project into a separate git repository
at `.zill/checkpoints`. Your own `.git` is never touched. You need git installed.

```sh
zill checkpoints -d ./myproject     # list snapshots
zill undo -d ./myproject            # undo the latest change (repeat to go further back)
zill undo 3f2a1bc -d ./myproject    # restore a specific snapshot
```

In interactive mode, use `/undo`, `/checkpoints`, and `/todo`. `/todo` shows
the checklist the agent keeps during long tasks.

### See what happened

Every tool call is written to `.zill/audit.jsonl` with its risk
(`read`, `write`, `execute`, `network`, `destructive`), the policy decision,
duration and status, with secrets redacted. It's the quickest way to see what
the agent did and why.

## Anatomy

| File | What it adds |
|---|---|
| `provider.py` | One neutral `complete()` in front of every model API. It reads `provider:model`, finds the key, and forwards to an adapter. |
| `providers/` | One adapter per wire format (`gemini`, `anthropic`, `openai_compat`) plus shared HTTP retries that respect server-requested delays and never retry a daily-quota 429. |
| `credentials.py` | Keys from the environment or `~/.zill/credentials.json` (mode 0600), and `redact()`, which masks every known secret before anything is printed. |
| `loop.py` | The agent loop: call the model, run the tools it asks for, repeat. Tool errors become results the model reads, never crashes. |
| `tools.py` | The `@tool` decorator and six core tools: read, write, edit, bash, list, grep. File paths are confined to the workdir. |
| `security.py` | `Policy.decide()`: classifies each call by risk (bash by what the command does), denies destructive calls in every mode, and applies `read-only`, `safe`, `yolo` and dry-run. |
| `audit.py` | `.zill/audit.jsonl`: one redacted line per tool call with risk, decision, duration and status. |
| `config.py` | `.zill/project.json`: the project's verify command and hooks, validated with clear errors. |
| `hooks.py` | Before and after hooks around state-changing tools, with safe `{path}` substitution. |
| `checkpoints.py` | Shadow-git snapshots before every change, with undo one step at a time. Your own `.git` is untouched. |
| `todo.py` | The checklist tool. It survives compaction and resume. |
| `context.py` | Compaction. Over the token budget, older turns are summarised and the recent tail is kept verbatim. |
| `memory.py` | `ZILL.md` project memory, loaded into the system prompt, and the `remember` tool that appends to it. |
| `skills.py` | `skills/<name>/SKILL.md`. A one-line catalog sits in the prompt, and the full text loads on demand via `use_skill`. |
| `session.py` | Append-only JSONL transcripts in `.zill/sessions`. Loading repairs a torn tail and unanswered tool calls. |
| `subagent.py` | `spawn_agent`: delegates a self-contained task to a child with a clean context. Depth is bounded. |
| `harness.py` | `Harness` wires everything to one workdir and flushes every message to disk before the next step. |
| `cli.py` | The front door: headless `-p`, the interactive loop, the approval prompt, and `--resume`. |
| `fleet.py` | `run_fleet`: many harnesses in many directories on a thread pool. |

## Use it as a library

`Harness` takes extra tools, so a project-specific capability takes a few lines:

```python
import urllib.request

from zill import Harness, Policy, tool


@tool("Fetch a URL and return the first 4000 characters of its body.",
      risk="network", url="An http or https URL")
def fetch(url):
    if not url.startswith(("http://", "https://")):
        return "ERROR: only http and https URLs are allowed"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read(4000).decode("utf-8", errors="replace")


agent = Harness("./research", policy=Policy("yolo"), extra_tools=[fetch])
print(agent.run("Fetch https://example.com and summarise it in NOTES.md"))
```

To run many agents at once, give `run_fleet` a factory:

```python
from zill import Harness, run_fleet

jobs = [{"name": "api", "workdir": "./api", "task": "add request logging"},
        {"name": "web", "workdir": "./web", "task": "fix the failing build"}]
for result in run_fleet(jobs, lambda workdir: Harness(workdir)):
    print(result["name"], result["ok"], result["report"][:80])
```

## Security

The file tools can't reach outside the workdir, but **`bash` is not a sandbox**.
Deny patterns catch obviously destructive commands only. Use `safe` mode for
anything you care about, and read [SECURITY.md](SECURITY.md).

## Contributing

ZILL stays small on purpose: zero runtime dependencies and a hard line budget
for the core. Fork it, branch, and send a pull request. Start with
[CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## License

[MIT](LICENSE) © 2026 Akbar Sheikh and ZILL Harness contributors.
