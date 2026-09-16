"""Settings: one place that decides the model, mode and profile of a run.

Concept: a run's settings come from command-line flags, the environment,
.zill/project.json, ~/.zill/config.json and built-in defaults. The CLI and
every subcommand ask resolve(), so `zill`, `zill inspect` and `zill doctor`
always agree on what a run would do.

Design rules:
  * model: flag, then ZILL_MODEL in the environment, then the project file,
    then the user file, then the default of the first provider with a key.
  * profile: flag, then the project file, then the user file, then "coding".
  * mode: a flag wins outright. Otherwise the user's default (or safe for a
    person, yolo headless) applies, and the project file and the profile may
    only make it stricter, never looser.
"""

import os

from . import config, provider
from .harness import Harness
from .profiles import PROFILES
from .security import Policy

STRICTNESS = {"read-only": 0, "safe": 1, "yolo": 2}


def resolve(workdir=".", model=None, mode=None, profile=None, headless=False):
    """Return {"model", "mode", "profile", "project", "user", "notes"} for a run in workdir."""
    workdir = os.path.realpath(workdir)
    user, project = config.load_user(), config.load_project(workdir)
    notes = []
    profile = profile or project.get("profile") or user.get("profile") or "coding"
    model = (model or os.environ.get("ZILL_MODEL") or project.get("model")
             or user.get("model") or provider.default_model())
    if not mode:
        mode = user.get("mode") or ("yolo" if headless else "safe")
        for source, stricter in (("project", project.get("mode")),
                                 (f"profile {profile}", PROFILES[profile]["mode"])):
            if stricter and STRICTNESS[stricter] < STRICTNESS[mode]:
                mode = stricter
            elif stricter and STRICTNESS[stricter] > STRICTNESS[mode]:
                notes.append(f"{source} asks for {stricter} mode; ignored, it may only "
                             f"tighten ({mode})")
    return {"workdir": workdir, "model": model, "mode": mode, "profile": profile,
            "project": project, "user": user, "notes": notes}


def make_harness(settings, approver=None, dry_run=False, plan=False, **kwargs):
    """Build the Harness a run with these settings would use, without calling a model."""
    problem = provider.check_model(settings["model"])
    if problem:
        raise RuntimeError(f"{problem}. Run `zill setup`, or pass -m provider:model.")
    profile = PROFILES[settings["profile"]]
    kwargs.setdefault("system_extra", profile["prompt"])
    policy = Policy(settings["mode"], approver=approver, dry_run=dry_run, plan=plan)
    return Harness(settings["workdir"], model=settings["model"], policy=policy,
                   allowed_tools=profile["tools"], **kwargs)
