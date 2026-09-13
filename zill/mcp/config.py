"""MCP config: the servers a project uses, and whether the user trusts them.

Concept: .zill/mcp.json lists servers by name: the command to start, its
arguments, the environment variables it may see, which of its tools are
read-only, and a timeout. A server only starts once the user has approved
that exact configuration (see zill.trust).

Design rules:
  * Strict validation with errors naming the file and the server.
  * Names are limited to letters, digits, "_" and "-", so namespaced tool
    names stay valid for every provider.
  * The trust key is the project path plus the server name, and the
    fingerprint covers the whole server entry: any edit needs re-approval.
"""

import json
import os
import re

from .. import trust

MCP_FILE = ".zill/mcp.json"
SERVER_KEYS = {"command", "args", "env", "read_tools", "timeout"}
NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")


def path(workdir):
    """Return the project's MCP config path."""
    return os.path.join(workdir, MCP_FILE)


def load(workdir):
    """Return {name: server} from .zill/mcp.json ({} when there is none)."""
    file = path(workdir)
    try:
        with open(file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise RuntimeError(f"cannot read {file}: {err}") from err
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or set(data) - {"servers"}:
        raise RuntimeError(f"{file} must be {{\"servers\": {{name: {{command, ...}}}}}}")
    for name, server in servers.items():
        where = f"{file}: server {name!r}"
        if not NAME.fullmatch(name):
            raise RuntimeError(f"{where}: names use letters, digits, _ and - (max 32)")
        if not isinstance(server, dict) or not isinstance(server.get("command"), str):
            raise RuntimeError(f"{where} needs a \"command\" string")
        if set(server) - SERVER_KEYS:
            raise RuntimeError(f"{where} has unknown keys {sorted(set(server) - SERVER_KEYS)}")
        lists = (server.get("args", []), server.get("read_tools", []))
        if not all(isinstance(v, list) and all(isinstance(i, str) for i in v) for v in lists):
            raise RuntimeError(f"{where}: \"args\" and \"read_tools\" must be lists of strings")
        if not isinstance(server.get("env", {}), dict):
            raise RuntimeError(f"{where}: \"env\" must map names to values")
        if not isinstance(server.get("timeout", 60), (int, float)):
            raise RuntimeError(f"{where}: \"timeout\" must be a number of seconds")
    return servers


def save(workdir, servers):
    """Write servers to .zill/mcp.json."""
    file = path(workdir)
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file, "w", encoding="utf-8") as f:
        json.dump({"servers": servers}, f, indent=2)


def trust_key(workdir, name):
    """Return the trust record key for a server in a project."""
    return f"{os.path.realpath(workdir)}::{name}"


def fingerprint(server):
    """Return the fingerprint of a server entry."""
    return trust.fingerprint(json.dumps(server, sort_keys=True))


def is_trusted(workdir, name, server):
    """True if the user approved exactly this server entry for this project."""
    return trust.is_trusted("mcp", trust_key(workdir, name), fingerprint(server))


def approve(workdir, name, server):
    """Record the user's approval of this server entry for this project."""
    trust.grant("mcp", trust_key(workdir, name), fingerprint(server))
