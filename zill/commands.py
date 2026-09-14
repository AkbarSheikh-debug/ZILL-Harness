"""Commands: the zill subcommands that are not a conversation.

Concept: around the agent loop sit the jobs a person does before and after a
run: paste keys (setup), check the machine (doctor), see what an agent would
have access to (inspect), find old transcripts (sessions), take changes back
(checkpoints, undo) and run many tasks at once (fleet).

Design rules:
  * Each command is a function argv -> exit code. People get short lines;
    --json gives scripts one parseable object.
  * Nothing here prints a secret: doctor and inspect say whether a key is
    set, never what it is.
  * inspect and fleet build their Harness through settings, exactly as a run
    does, and inspect never calls a model or writes a file.
"""

import argparse
import getpass
import importlib
import json
import os
import platform
import shutil
import sys

from . import __version__, credentials, history, provider, session, settings, skills
from .checkpoints import Checkpoints
from .fleet import run_fleet
from .profiles import PROFILES
from .security import MODES

UI_INSTALL = 'pip install "zill-harness[ui]"   (with uv: uv tool install zill-harness --with zill-ui)'


def _parser(name, description, json_flag=True):
    """Return an argument parser for `zill name` with the shared -d flag."""
    parser = argparse.ArgumentParser(prog=f"zill {name}", description=description)
    parser.add_argument("-d", "--workdir", default=".", help="project directory")
    if json_flag:
        parser.add_argument("--json", action="store_true", help="print one JSON object")
    return parser


def _emit(data, as_json, lines):
    """Print data as JSON, or the human-readable lines."""
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print("\n".join(lines))


def setup(argv=()):
    """Ask for each provider's API key with hidden input and save the ones given."""
    print(f"ZILL setup: paste an API key for each provider you use (input is hidden; "
          f"Enter skips).\nKeys are saved to {credentials.path()}")
    try:
        for name, (_, _, key_var, _) in provider.PROVIDERS.items():
            if key_var:
                _ask_key(name, key_var)
        saved = provider.default_model()
        suggested = provider.default_model(saved=False) if provider.check_model(saved) else saved
        print(f"Models are written provider:model, e.g. {provider.DEFAULT_MODEL}.")
        while True:
            model = input(f"Default model [{suggested}]: ").strip() or suggested
            problem = provider.check_model(model)
            if not problem:
                break
            print(f"  {problem}")
    except (EOFError, KeyboardInterrupt):
        print("\nsetup stopped; keys entered so far are saved")
        return 1
    if model != saved:
        credentials.save("ZILL_MODEL", model)
    missing = provider.missing_key(model)
    if missing:
        print(f"{model} still needs {missing}. Run `zill setup` again to add it.")
        return 1
    print(f"Ready. Model: {model}.")
    return 0


def _ask_key(name, key_var):
    """Ask for one provider's key until it is skipped, accepted, or cannot be checked."""
    status = " [already set]" if credentials.get(key_var) else ""
    while True:
        value = getpass.getpass(f"  {name} {key_var}{status}: ").strip()
        if not value:
            return
        try:
            problem = provider.check_key(name, value)
            note = "ok"
        except RuntimeError as err:
            problem, note = None, f"saved, but it could not be checked now: {err}"
        if not problem:
            credentials.save(key_var, value)
            print(f"    {note}")
            return
        print(f"    {problem}\n    not saved: paste another key, or press Enter to skip")


def doctor(argv):
    """Check Python, git, config, keys and permissions; exit 1 if anything would fail."""
    parser = _parser("doctor", "Check that ZILL is ready to run in a directory.")
    parser.add_argument("-m", "--model", help="check this model instead of the default")
    args = parser.parse_args(argv)
    workdir = os.path.realpath(args.workdir)
    checks = []

    def check(status, name, detail):
        checks.append({"status": status, "check": name, "detail": detail})

    check("ok" if sys.version_info >= (3, 10) else "fail", "python",
          f"{platform.python_version()} (3.10 or newer needed)")
    check("ok", "zill", __version__)
    if not os.path.isdir(workdir):
        check("fail", "workdir", f"{workdir} does not exist")
    else:
        writable = os.access(workdir, os.W_OK)
        check("ok" if writable else "fail", "workdir",
              f"{workdir} is {'writable' if writable else 'not writable'}")
    check("ok" if shutil.which("git") else "warn", "git",
          "found: checkpoints and undo are on" if shutil.which("git")
          else "not found: install git to enable checkpoints and undo")
    try:
        with_keys = [name for name, (_, _, var, _) in provider.PROVIDERS.items()
                     if var and credentials.get(var)]
        check("ok" if with_keys else "warn", "keys",
              f"set for {', '.join(with_keys)}" if with_keys
              else "none set: run `zill setup` (local ollama and lmstudio need none)")
        resolved = settings.resolve(workdir, model=args.model) if os.path.isdir(workdir) else None
    except RuntimeError as err:
        check("fail", "config", str(err))
        resolved = None
    if resolved:
        check("ok", "config", f"model {resolved['model']}, mode {resolved['mode']}, "
                              f"profile {resolved['profile']}")
        for note in resolved["notes"]:
            check("warn", "config", note)
        problem = provider.check_model(resolved["model"])
        missing = not problem and provider.missing_key(resolved["model"])
        check("fail" if problem or missing else "ok", "model",
              f"{problem}: run `zill setup`" if problem else
              f"{resolved['model']} needs {missing}: run `zill setup`" if missing
              else f"{resolved['model']} has what it needs")
        project = resolved["project"]
        check("ok", "project", f"verify: {project['verify'] or 'none'}; "
                               f"hooks: {len(project['hooks'])}")
        from . import mcp, plugins  # checks read config only; nothing is started or imported
        try:
            servers = mcp.config.load(workdir)
            waiting = [n for n, s in servers.items() if not mcp.config.is_trusted(workdir, n, s)]
            check("warn" if waiting else "ok", "mcp",
                  f"{len(servers)} servers" + (f"; not approved: {', '.join(waiting)} "
                                               f"(zill mcp trust NAME)" if waiting else ""))
        except RuntimeError as err:
            check("fail", "mcp", str(err))
        for folder, manifest, error in plugins.discover(workdir):
            if error:
                check("warn", "plugin", error)
            elif plugins.state(folder) == "changed":
                check("warn", "plugin", f"{manifest['name']} changed since it was enabled")
    path = credentials.path()
    if os.name == "posix" and os.path.exists(path) and os.stat(path).st_mode & 0o077:
        check("warn", "credentials", f"{path} is readable by others: chmod 600 {path}")
    failed = any(c["status"] == "fail" for c in checks)
    _emit({"ok": not failed, "checks": checks}, args.json,
          [f"{c['status']:<5} {c['check']}: {c['detail']}" for c in checks])
    return 1 if failed else 0


def inspect(argv):
    """Show what a run here would have: model, policy, tools and their risk, skills, session."""
    parser = _parser("inspect", "Show what an agent run in a directory would have access to.")
    parser.add_argument("-m", "--model")
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--profile", choices=list(PROFILES))
    args = parser.parse_args(argv)
    if not os.path.isdir(args.workdir):
        print(f"error: {args.workdir} does not exist", file=sys.stderr)
        return 1
    try:
        resolved = settings.resolve(args.workdir, args.model, args.mode, args.profile)
        harness = settings.make_harness(resolved, persist=False)  # starts approved MCP servers
        missing = provider.missing_key(harness.model)
    except RuntimeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    harness.close()
    key_var = provider.resolve(harness.model)[3]
    info = {
        "model": harness.model, "provider": provider.resolve(harness.model)[0],
        "key": "not needed" if key_var is None else ("missing" if missing else "set"),
        "workdir": harness.workdir, "mode": harness.policy.mode, "profile": resolved["profile"],
        "verify": harness.verify, "hooks": len(harness.hooks),
        "checkpoints": bool(harness.checkpoints and harness.checkpoints.available),
        "memory": os.path.isfile(os.path.join(harness.workdir, "ZILL.md")),
        "tools": [{"name": n, "source": t.source, "risk": t.risk}
                  for n, t in sorted(harness.tools.items())],
        "skills": sorted(skills.catalog(harness.workdir)),
        "latest_session": session.latest(harness.workdir),
        "notes": resolved["notes"] + harness.notes,
    }
    lines = [f"{field:<15}{info[field]}" for field in
             ("model", "provider", "key", "workdir", "mode", "profile", "verify", "hooks",
              "checkpoints", "memory", "latest_session")]
    lines.append("tools:")
    lines += [f"  {t['name']:<14} {t['risk']:<12} {t['source']}" for t in info["tools"]]
    lines.append(f"skills:        {', '.join(info['skills']) or 'none'}")
    lines += [f"note: {note}" for note in info["notes"]]
    _emit(info, args.json, lines)
    return 0


def sessions(argv):
    """List saved sessions in a directory, newest first."""
    args = _parser("sessions", "List saved sessions, newest first.").parse_args(argv)
    rows = history.session_rows(args.workdir)
    lines = [f"{r['modified']}  {r['messages']:>4} msgs  {r['model'] or '?':<32} "
             f"{credentials.redact(r['title'])}" for r in rows] or ["no sessions yet"]
    _emit(rows, args.json, lines)
    return 0


def checkpoints(argv):
    """List snapshots taken before the agent's changes."""
    args = _parser("checkpoints", "List checkpoints, newest first.").parse_args(argv)
    entries = Checkpoints(args.workdir).list()
    _emit([{"id": ref, "age": age, "label": label} for ref, age, label in entries], args.json,
          [f"{ref}  {age:>16}  {label}" for ref, age, label in entries] or ["no checkpoints yet"])
    return 0


def undo(argv):
    """Restore a checkpoint: the latest change by default, or the id given."""
    parser = _parser("undo", "Undo the agent's latest change, or restore a checkpoint.",
                     json_flag=False)
    parser.add_argument("id", nargs="?", help="checkpoint to restore (default: latest change)")
    args = parser.parse_args(argv)
    return restore(Checkpoints(args.workdir), args.id)


def restore(store, ref=None):
    """Restore a checkpoint and report it; return an exit code."""
    try:
        restored = store.restore(ref)
    except RuntimeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(f"restored checkpoint {restored}. To reverse this, run `zill checkpoints` "
          f"and restore the \"restore:\" entry.")
    return 0


def fleet(argv):
    """Run every job in a JSON file in parallel: [{"name", "workdir", "task"}, ...]."""
    parser = argparse.ArgumentParser(prog="zill fleet",
                                     description="Run many tasks in many directories at once.")
    parser.add_argument("jobs", help="JSON file: a list of {name, workdir, task}")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("-m", "--model")
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--profile", choices=list(PROFILES))
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    try:
        with open(args.jobs, encoding="utf-8") as f:
            jobs = json.load(f)
    except (OSError, ValueError) as err:
        print(f"error: cannot read {args.jobs}: {err}", file=sys.stderr)
        return 1
    if not (isinstance(jobs, list) and all(isinstance(j, dict) and
                                           {"name", "workdir", "task"} <= set(j) for j in jobs)):
        print(f"error: {args.jobs} must be a list of {{name, workdir, task}} objects",
              file=sys.stderr)
        return 1
    base = os.path.dirname(os.path.abspath(args.jobs))

    def make_harness(workdir):
        resolved = settings.resolve(os.path.join(base, workdir), args.model, args.mode,
                                    args.profile, headless=True)
        return settings.make_harness(resolved)

    harnesses = []

    def make_tracked(workdir):
        harnesses.append(make_harness(workdir))
        return harnesses[-1]

    try:
        results = run_fleet(jobs, make_tracked, max_workers=args.workers)
    finally:
        for harness in harnesses:
            harness.close()
    for result in results:
        result["report"] = credentials.redact(result["report"])
    _emit(results, args.json,
          [f"{'ok' if r['ok'] else 'FAIL':<5}{r['name']}: "
           f"{(r['report'].splitlines() or [''])[0][:100]}" for r in results])
    return 0 if all(r["ok"] for r in results) else 1


def mcp(argv):
    """`zill mcp ...`: manage and serve MCP servers."""
    from .mcp import cli as mcp_cli
    return mcp_cli.main(argv)


def plugin(argv):
    """`zill plugin ...`: manage plugins and connectors."""
    from .plugins import cli as plugin_cli
    return plugin_cli.main(argv)


def ui(argv):
    """`zill ui`: the same agent in a local browser app, from the optional zill-ui package."""
    try:
        app = importlib.import_module("zill_ui")  # optional: ZILL itself needs nothing extra
    except ImportError:
        print(f"The ZILL app is a separate package. Add it with:\n  {UI_INSTALL}\n"
              f"then run `zill ui` again.", file=sys.stderr)
        return 1
    return app.main(argv)


COMMANDS = {"setup": setup, "doctor": doctor, "inspect": inspect, "sessions": sessions,
            "checkpoints": checkpoints, "undo": undo, "fleet": fleet, "mcp": mcp,
            "plugin": plugin, "ui": ui}
