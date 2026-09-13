"""Plugins and connectors: local Python extensions that add tools.

Concept: a plugin is a folder with a manifest.json and a Python module whose
register(registry) adds tools. A connector is a plugin that integrates an
external service and declares the credentials it needs. Both live in
.zill/plugins/<name>/ (this project) or ~/.zill/plugins/<name>/ (every project).

Design rules:
  * Loading a Python plugin is not a security boundary: it runs with ZILL's
    privileges. So nothing is imported until the user enables it (zill plugin
    enable), and the approval pins a fingerprint of every file in the folder;
    any edit disables it again until re-approved.
  * register() gets a Registry, not the Harness. A tool's risk must be one of
    the permissions the manifest declared, and its name is namespaced
    (plugin__<name>__<tool>, connector__<name>__<tool>), so an extension can
    never replace a builtin.
  * A connector whose credential is missing still loads; its tools answer
    "ERROR: <name> connector is not configured" instead of failing.
  * A broken plugin is skipped with a note; it never stops the harness.
"""

import importlib.util
import json
import os
import re

from .. import credentials, trust
from ..security import RISKS
from ..tools import Tool

MANIFEST = "manifest.json"
PLUGIN_DIR = os.path.join(".zill", "plugins")
NAME = re.compile(r"[a-z0-9_-]{1,32}")
ENTRYPOINT = re.compile(r"([A-Za-z_]\w*):([A-Za-z_]\w*)")
KEYS = {"name", "version", "description", "entrypoint", "kind", "permissions", "credentials"}


def roots(workdir):
    """Return the plugin folders searched: the project's, then the user's."""
    return [os.path.join(workdir, PLUGIN_DIR), os.path.join(credentials.home(), "plugins")]


def read_manifest(folder):
    """Return the validated manifest of the plugin in folder, or raise RuntimeError."""
    path = os.path.join(folder, MANIFEST)
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as err:
        raise RuntimeError(f"cannot read {path}: {err}") from err
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    problems = []
    if set(manifest) - KEYS:
        problems.append(f"unknown keys {sorted(set(manifest) - KEYS)}")
    if not NAME.fullmatch(str(manifest.get("name", ""))):
        problems.append("\"name\" must be lowercase letters, digits, _ or - (max 32)")
    if not ENTRYPOINT.fullmatch(str(manifest.get("entrypoint", ""))):
        problems.append("\"entrypoint\" must look like \"module:function\"")
    if manifest.get("kind", "plugin") not in ("plugin", "connector"):
        problems.append("\"kind\" must be \"plugin\" or \"connector\"")
    permissions = manifest.get("permissions", [])
    if not (isinstance(permissions, list) and set(permissions) <= set(RISKS)):
        problems.append(f"\"permissions\" must be a list drawn from {', '.join(RISKS)}")
    if not isinstance(manifest.get("credentials", []), list):
        problems.append("\"credentials\" must be a list of variable names")
    if problems:
        raise RuntimeError(f"{path}: " + "; ".join(problems))
    return manifest


def discover(workdir):
    """Return [(folder, manifest or None, error or None)] for every plugin folder found."""
    found = []
    for root in roots(workdir):
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            folder = os.path.realpath(os.path.join(root, entry))
            if os.path.isfile(os.path.join(folder, MANIFEST)):
                try:
                    found.append((folder, read_manifest(folder), None))
                except RuntimeError as err:
                    found.append((folder, None, str(err)))
    return found


def fingerprint(folder):
    """Return a fingerprint of every file in folder (caches excluded)."""
    parts = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            with open(path, "rb") as f:
                parts += [os.path.relpath(path, folder).replace(os.sep, "/"), f.read()]
    return trust.fingerprint(*parts)


def state(folder):
    """Return "enabled", "changed" (enabled, then edited) or "disabled"."""
    approved = trust.recorded("plugins", folder)
    if approved is None:
        return "disabled"
    return "enabled" if approved == fingerprint(folder) else "changed"


class Registry:
    """What a plugin's register() may do: add tools and read its declared credentials.

    registry.workdir is the project directory, for plugins that keep files there.
    """

    def __init__(self, manifest, workdir):
        self.manifest, self.workdir, self.tools = manifest, workdir, []
        self.prefix = f"{manifest.get('kind', 'plugin')}__{manifest['name']}__"

    def credential(self, name):
        """Return a declared credential's value, or None when it is not set."""
        if name not in self.manifest.get("credentials", []):
            raise RuntimeError(f"{self.manifest['name']} did not declare the credential {name}")
        return credentials.get(name)

    def add_tool(self, tool):
        """Register a Tool made with @tool; its risk must be a declared permission."""
        if tool.risk not in self.manifest.get("permissions", []):
            raise RuntimeError(f"{self.manifest['name']}: tool {tool.name} needs the "
                               f"\"{tool.risk}\" permission, which the manifest does not declare")
        missing = [c for c in self.manifest.get("credentials", []) if not credentials.get(c)]
        run = tool.run
        if missing:
            def run(**_ignored):
                return (f"ERROR: {self.manifest['name']} connector is not configured: "
                        f"set {', '.join(missing)} (with `zill setup` or the environment)")
        name = f"{self.prefix}{tool.name}"[:64]
        schema = {**tool.spec["schema"], "name": name}
        self.tools.append(Tool(name=name, spec={"schema": schema}, run=run,
                               source=self.manifest.get("kind", "plugin"), risk=tool.risk))


def load_plugin(folder, manifest, workdir):
    """Import folder's entrypoint and run register(); return its tools."""
    module_name, function = manifest["entrypoint"].split(":")
    path = os.path.join(folder, f"{module_name}.py")
    if not os.path.isfile(path):
        raise RuntimeError(f"{path} not found")
    spec = importlib.util.spec_from_file_location(f"zill_plugin_{manifest['name']}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = Registry(manifest, workdir)
    getattr(module, function)(registry)
    return registry.tools


def load_tools(workdir):
    """Load every enabled plugin; return (tools, notes)."""
    tools, notes, loaded = [], [], set()
    for folder, manifest, error in discover(workdir):
        if error:
            notes.append(f"plugin skipped: {error}")
            continue
        name, current = manifest["name"], state(folder)
        if current == "changed":
            notes.append(f"plugin {name} changed since it was enabled; review it, then run "
                         f"`zill plugin enable {name}`")
        if current != "enabled":
            continue
        if name in loaded:
            notes.append(f"plugin {name} in {folder} ignored: another {name} is already loaded")
            continue
        try:
            tools += load_plugin(folder, manifest, workdir)
            loaded.add(name)
        except Exception as err:  # a broken plugin is a note, never a crash
            notes.append(f"plugin {name} failed to load: {type(err).__name__}: {err}")
    return tools, notes
