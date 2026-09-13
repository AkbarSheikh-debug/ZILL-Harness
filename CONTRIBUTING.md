# Contributing to ZILL Harness

Thanks for helping. ZILL has one promise that every change must keep:
**a capable harness product built with less code.** Keep the core small and
put interoperability at the edges. The full plan is in [ROADMAP.md](ROADMAP.md).

## The compact rules

1. **Zero runtime dependencies.** Standard library only. `dependencies = []`
   in `pyproject.toml` stays empty.
2. **Core line budget: 3,500 lines** across `zill/*.py`. CI fails above it.
   If your feature needs more room, make it smaller or propose an optional module.
3. **One concept per file.** Each module opens with a docstring stating the
   concept it implements and the design rules it keeps. New modules do the same.
4. **Match the surrounding code.** Same naming, comment density, and idioms.
   Prefer deleting code to adding it.
5. **Tools never crash the loop.** Recoverable misuse returns an `"ERROR: ..."`
   string that tells the model how to fix its next call.
6. **Output is bounded.** Nothing a tool returns may flood the context window.

## Workflow

1. **Fork** the repo and clone your fork.
2. **Branch** from `main` using a prefix:
   `feat/…`, `fix/…`, `docs/…`, `refactor/…`, `test/…`, `provider/…`
3. **Install** in editable mode:
   ```sh
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -e .
   zill --help
   ```
4. **Make one focused change.** Small pull requests get merged quickly, and large ones stall.
5. **Check** before pushing:
   ```sh
   python -m compileall -q zill
   python -m unittest discover          # offline; tests/fake.py scripts the model
   python -c "import pathlib; print(sum(len(p.read_text(encoding='utf-8').splitlines()) for p in pathlib.Path('zill').glob('*.py')))"
   ```
6. **Commit** with a clear imperative message (`Add OpenAI-compatible provider`).
7. **Open a pull request** against `main` and fill in the template.

## What to work on

- Anything in [ROADMAP.md](ROADMAP.md). Provider adapters and the offline
  test suite are the most wanted.
- Issues labelled `good first issue` or `help wanted`.
- For a new feature, **open an issue first** so we can agree on the design
  before you spend time on it.

## Adding a provider

`provider.py` is the only file allowed to know a model's wire format. A new
provider takes neutral messages `{"role", "text", "tool_calls"}` and returns
`{"text", "tool_calls", "usage"}`. It must not add imports or logic
anywhere else.

## Reporting bugs

Use the bug report template. Include your OS, Python version, model, the
command you ran, and the smallest steps that reproduce it. **Never paste API keys**,
and remove them from any logs or session files you attach.

## Code of Conduct

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
