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

### Changed
- The system prompt names the real shell the `bash` tool runs (`COMSPEC` on
  Windows) and asks for Windows command syntax there.

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
