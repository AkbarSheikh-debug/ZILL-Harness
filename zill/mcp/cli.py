"""`zill mcp`: add, trust, list, remove and serve MCP servers.

Design rules:
  * Adding a server records it in .zill/mcp.json and approves exactly that
    entry, because the user typed the command. A server that arrived with a
    cloned project needs `zill mcp trust NAME`, which shows the command first.
  * `zill mcp list --tools` connects to each approved server to show its
    tools; plain `list` starts nothing.
"""

import argparse
import os
import sys

from ..security import MODES
from . import config, make_tools, server
from .client import MCPClient, MCPError


def build_parser():
    """Return the parser for `zill mcp ...`."""
    parser = argparse.ArgumentParser(prog="zill mcp", description="Manage MCP servers.")
    sub = parser.add_subparsers(dest="action", required=True)

    def action(name, help_text):
        each = sub.add_parser(name, help=help_text)
        each.add_argument("-d", "--workdir", default=".", help="project directory")
        return each

    add = action("add", "add and approve a server: zill mcp add NAME [options] -- COMMAND ...")
    add.add_argument("name")
    add.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                     help="pass one variable; VALUE may be ${VAR} (repeatable)")
    add.add_argument("--read-tools", default="", help="comma-separated tools that only read")
    add.add_argument("--timeout", type=float, default=60)
    add.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command to run")
    action("trust", "approve a server that came with the project").add_argument("name")
    action("remove", "remove a server").add_argument("name")
    action("list", "list servers").add_argument(
        "--tools", action="store_true", help="connect to approved servers and list their tools")
    serve = action("serve", "offer ZILL's tools to another MCP client over stdio")
    serve.add_argument("--tools", default=",".join(server.DEFAULT_TOOLS),
                       help=f"comma-separated tools (default {','.join(server.DEFAULT_TOOLS)})")
    serve.add_argument("--mode", choices=MODES, default="read-only")
    return parser


def main(argv):
    """Dispatch `zill mcp ACTION`; return an exit code."""
    args = build_parser().parse_args(argv)
    workdir = os.path.realpath(args.workdir)
    try:
        return ACTIONS[args.action](args, workdir)
    except RuntimeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def _add(args, workdir):
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise RuntimeError("give the command after --, e.g. zill mcp add notes -- python server.py")
    env = dict(item.split("=", 1) for item in args.env if "=" in item)
    entry = {"command": command[0], "args": command[1:], "env": env,
             "read_tools": [t for t in args.read_tools.split(",") if t], "timeout": args.timeout}
    servers = config.load(workdir)
    servers[args.name] = entry
    config.save(workdir, servers)
    config.load(workdir)  # validate what was written
    config.approve(workdir, args.name, entry)
    print(f"added and approved MCP server {args.name}: {' '.join(command)}")
    return 0


def _trust(args, workdir):
    servers = config.load(workdir)
    if args.name not in servers:
        raise RuntimeError(f"no MCP server named {args.name} in {config.path(workdir)}")
    entry = servers[args.name]
    print(f"{args.name} runs: {entry['command']} {' '.join(entry.get('args', []))}\n"
          f"environment: {', '.join(entry.get('env', {})) or 'only the basics'}")
    try:
        answer = input("Approve this server to start whenever ZILL runs here? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("not approved")
        return 1
    config.approve(workdir, args.name, entry)
    print(f"approved {args.name}")
    return 0


def _remove(args, workdir):
    servers = config.load(workdir)
    if servers.pop(args.name, None) is None:
        raise RuntimeError(f"no MCP server named {args.name}")
    config.save(workdir, servers)
    print(f"removed {args.name}")
    return 0


def _list(args, workdir):
    servers = config.load(workdir)
    if not servers:
        print("no MCP servers (add one with `zill mcp add NAME -- COMMAND`)")
    for name, entry in servers.items():
        state = "approved" if config.is_trusted(workdir, name, entry) else "NOT approved"
        print(f"{name:<16} {state:<13} {entry['command']} {' '.join(entry.get('args', []))}")
        if args.tools and state == "approved":
            client = MCPClient(name, entry["command"], entry.get("args", []), entry.get("env"),
                               cwd=workdir, timeout=entry.get("timeout", 60))
            try:
                client.start()
                for tool in make_tools(client, entry.get("read_tools", [])):
                    print(f"    {tool.name:<40} {tool.risk}")
            except MCPError as err:
                print(f"    unavailable: {err}")
            finally:
                client.close()
    return 0


def _serve(args, workdir):
    return server.serve(workdir, [t for t in args.tools.split(",") if t], args.mode)


ACTIONS = {"add": _add, "trust": _trust, "remove": _remove, "list": _list, "serve": _serve}
