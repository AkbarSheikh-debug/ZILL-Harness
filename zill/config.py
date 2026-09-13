"""Project config: the commands a project asks ZILL to run for it.

Concept: .zill/project.json in the working directory declares project
automation: the verify command that proves work is done, and hooks that run
around tools. ZILL works the same without the file.

Design rules:
  * JSON, parsed with the standard library. Every problem is a clear error
    naming the file and the field, never a silently ignored setting.
  * The file only declares commands. Harness runs them, and every run goes
    through Policy like a bash call, so a config file in a cloned repository
    cannot run anything the user did not allow.
"""

import json
import os

PROJECT_FILE = ".zill/project.json"
HOOK_KEYS = {"when", "tool", "match", "run"}


def load_project(workdir):
    """Return {"verify": str or None, "hooks": [hook, ...]} from workdir's project file."""
    path = os.path.join(workdir, PROJECT_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"verify": None, "hooks": []}
    except (OSError, ValueError) as err:
        raise RuntimeError(f"cannot read {path}: {err}") from err
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
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
    return {"verify": verify, "hooks": hooks}
