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

Then add your API key and start it:

```sh
# macOS / Linux
export GEMINI_API_KEY="your-key"
zill
```

```powershell
# Windows PowerShell
$env:GEMINI_API_KEY = "your-key"
zill
```

### Providers

| Provider | Status | Key variable |
|---|---|---|
| Google Gemini | Supported | `GEMINI_API_KEY` or `ZILL_API_KEY` |
| Anthropic Claude | Planned (v0.2) | `ANTHROPIC_API_KEY` |
| OpenAI and OpenAI-compatible (OpenRouter, Groq, DeepSeek, Ollama, LM Studio) | Planned (v0.2) | `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, ... |

See [ROADMAP.md](ROADMAP.md). Provider adapters are a great first contribution.

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
| `-m, --model` | Model name (default `ZILL_MODEL` or `gemini-3.1-pro-preview`). |
| `--mode` | `safe`, `yolo` or `read-only`. Default: `safe` interactively, `yolo` with `-p`. |
| `--resume` | Load the newest session in the workdir before running. |
| `--max-turns` | Tool turns allowed per task (default 120). |

In safe mode, every call that changes state asks
`approve bash({"command": ...})? [y/N]`. Ctrl-C stops the current run without
losing the session. Ctrl-D exits.

## Anatomy

| File | What it adds |
|---|---|
| `provider.py` | The only code that knows the model's wire format. It sends neutral messages, gets neutral replies back, and retries transient errors. |
| `loop.py` | The agent loop: call the model, run the tools it asks for, repeat. Tool errors become results the model reads, never crashes. |
| `tools.py` | The `@tool` decorator and six core tools: read, write, edit, bash, list, grep. File paths are confined to the workdir. |
| `security.py` | `Policy`: deny patterns for catastrophic commands, plus `read-only`, `safe` and `yolo` modes with an approver hook. |
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
      url="An http or https URL")
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
