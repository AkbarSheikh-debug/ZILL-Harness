"""`zill plugin`: list, inspect, enable and disable plugins and connectors.

Design rules:
  * Enabling shows the manifest's permissions and credentials and asks for a
    yes (or --yes), then approves the plugin's current files only.
  * list and inspect never import plugin code.
"""

import argparse
import os
import sys

from .. import credentials, trust
from . import discover, fingerprint, state


def main(argv):
    """Dispatch `zill plugin ACTION`; return an exit code."""
    parser = argparse.ArgumentParser(prog="zill plugin",
                                     description="Manage plugins and connectors.")
    sub = parser.add_subparsers(dest="action", required=True)
    for name, help_text in (("list", "list plugins and whether they are enabled"),
                            ("inspect", "show a plugin's manifest"),
                            ("enable", "approve and enable a plugin"),
                            ("disable", "disable a plugin")):
        each = sub.add_parser(name, help=help_text)
        if name != "list":
            each.add_argument("name")
        if name == "enable":
            each.add_argument("--yes", action="store_true", help="do not ask for confirmation")
        each.add_argument("-d", "--workdir", default=".", help="project directory")
    args = parser.parse_args(argv)
    plugins = discover(os.path.realpath(args.workdir))
    if args.action == "list":
        for folder, manifest, error in plugins:
            label = manifest["name"] if manifest else os.path.basename(folder)
            status = "invalid" if error else state(folder)
            kind = manifest.get("kind", "plugin") if manifest else "?"
            print(f"{label:<20} {kind:<10} {status:<9} {folder}")
            if error:
                print(f"    {error}")
        if not plugins:
            print("no plugins found in .zill/plugins/ or ~/.zill/plugins/")
        return 0
    matches = [(f, m) for f, m, e in plugins if m and m["name"] == args.name]
    if not matches:
        print(f"error: no valid plugin named {args.name} (try `zill plugin list`)",
              file=sys.stderr)
        return 1
    folder, manifest = matches[0]
    if args.action == "disable":
        print(f"disabled {args.name}" if trust.revoke("plugins", folder)
              else f"{args.name} was not enabled")
        return 0
    creds = [f"{name} ({'set' if credentials.get(name) else 'missing'})"
             for name in manifest.get("credentials", [])]
    print(f"{manifest['name']} {manifest.get('version', '')} ({manifest.get('kind', 'plugin')})\n"
          f"  {manifest.get('description', '')}\n  folder: {folder}\n"
          f"  permissions: {', '.join(manifest.get('permissions', [])) or 'none'}\n"
          f"  credentials: {', '.join(creds) or 'none'}\n"
          f"  status: {state(folder)}")
    if args.action == "inspect":
        return 0
    print("Python plugins run with ZILL's full privileges; enable only code you trust.")
    if not args.yes:
        try:
            answer = input(f"Enable {manifest['name']}? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("not enabled")
            return 1
    trust.grant("plugins", folder, fingerprint(folder))
    print(f"enabled {manifest['name']}")
    return 0
