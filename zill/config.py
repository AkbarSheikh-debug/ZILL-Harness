"""Config: user defaults and project settings, validated before anything runs.

Concept: two optional JSON files shape a run. ~/.zill/config.json holds the
user's defaults (model, mode, profile, prices). .zill/project.json in the
working directory holds what a project needs: its verify command, hooks,
and the model, mode or profile it prefers. ZILL works the same without them.

Design rules:
  * JSON, parsed with the standard library. Every problem is a clear error
    naming the file and the field; an unknown key is an error, never a
    silently ignored setting.
  * The files only declare. Commands they name are run by Harness through
    Policy like bash calls, and a project file may only make the mode
    stricter (settings.resolve), so a cloned repository gets no special trust.
"""

import json
import os

from . import credentials
from .profiles import PROFILES
from .security import MODES

PROJECT_FILE = ".zill/project.json"
USER_FILE = "config.json"
HOOK_KEYS = {"when", "tool", "match", "run"}
SHARED_KEYS = {"model", "mode", "profile"}


def user_path():
    """Return the user config path, ~/.zill/config.json (or under ZILL_HOME)."""
    return os.path.join(credentials.home(), USER_FILE)


def load_user():
    """Return the validated user config as a dict ({} when there is no file)."""
    path = user_path()
    data = _read(path)
    _check_keys(path, data, SHARED_KEYS | {"prices"})
    _check_shared(path, data)
    prices = data.get("prices", {})
    valid = isinstance(prices, dict) and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(n, (int, float)) for n in p)
        for p in prices.values())
    if not valid:
        raise RuntimeError(f"{path}: \"prices\" must map \"provider:model\" to "
                           f"[input, output] USD per million tokens")
    return data


def load_project(workdir):
    """Return the validated project config, with "verify" and "hooks" always present."""
    path = os.path.join(workdir, PROJECT_FILE)
    data = _read(path)
    _check_keys(path, data, SHARED_KEYS | {"verify", "hooks"})
    _check_shared(path, data)
    verify = data.get("verify")
    if verify is not None and not (isinstance(verify, str) and verify.strip()):
        raise RuntimeError(f"{path}: \"verify\" must be a non-empty command string")
    hooks = data.get("hooks", [])
    if not isinstance(hooks, list):
        raise RuntimeError(f"{path}: \"hooks\" must be a list")
    for index, hook in enumerate(hooks):
        valid = (isinstance(hook, dict) and hook.get("when") in ("before", "after")
                 and isinstance(hook.get("run"), str) and hook["run"].strip())
        if not valid:
            raise RuntimeError(f"{path}: hooks[{index}] needs \"when\": \"before\" or "
                               f"\"after\" and a non-empty \"run\" command")
        unknown = set(hook) - HOOK_KEYS
        if unknown:
            raise RuntimeError(f"{path}: hooks[{index}] has unknown keys {sorted(unknown)}")
    return {**data, "verify": verify, "hooks": hooks}


def _read(path):
    """Return the JSON object in path, {} if it does not exist."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise RuntimeError(f"cannot read {path}: {err}") from err
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return data


def _check_keys(path, data, allowed):
    """Refuse keys this version does not understand."""
    unknown = set(data) - allowed
    if unknown:
        raise RuntimeError(f"{path}: unknown keys {sorted(unknown)} "
                           f"(allowed: {', '.join(sorted(allowed))})")


def _check_shared(path, data):
    """Validate the model, mode and profile fields both files may set."""
    if "model" in data and not (isinstance(data["model"], str) and data["model"].strip()):
        raise RuntimeError(f"{path}: \"model\" must be a non-empty string")
    if "mode" in data and data["mode"] not in MODES:
        raise RuntimeError(f"{path}: \"mode\" must be one of {', '.join(MODES)}")
    if "profile" in data and data["profile"] not in PROFILES:
        raise RuntimeError(f"{path}: \"profile\" must be one of {', '.join(PROFILES)}")
