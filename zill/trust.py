"""Trust: which MCP servers and plugins the user has approved to run.

Concept: a project can ship .zill/mcp.json and .zill/plugins/, and both name
code that would run the moment ZILL starts. Cloning a repository must never
be enough to run it. So approval lives in ~/.zill/trust.json, outside every
project, and each approval pins a fingerprint of exactly what was approved.

Design rules:
  * Nothing in a project directory can grant trust; only `zill mcp add`,
    `zill mcp trust` and `zill plugin enable` write this file.
  * An approval covers one fingerprint. A changed server command or an
    edited plugin file no longer matches, and needs approving again.
  * Written atomically, readable only by the owner, like credentials.
"""

import hashlib
import json
import os

from . import credentials

TRUST_FILE = "trust.json"


def path():
    """Return the trust record path, ~/.zill/trust.json (or under ZILL_HOME)."""
    return os.path.join(credentials.home(), TRUST_FILE)


def fingerprint(*parts):
    """Return a sha256 hex digest of the given strings or bytes, in order."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _load():
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise RuntimeError(f"cannot read {path()}: {err}") from err
    return data if isinstance(data, dict) else {}


def _save(data):
    os.makedirs(credentials.home(), exist_ok=True)
    temp = f"{path()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, path())


def recorded(kind, key):
    """Return the fingerprint the user approved for kind/key, or None."""
    return _load().get(kind, {}).get(key)


def is_trusted(kind, key, digest):
    """True if the user approved exactly this fingerprint for kind/key."""
    return recorded(kind, key) == digest


def grant(kind, key, digest):
    """Record the user's approval of this fingerprint for kind/key."""
    data = _load()
    data.setdefault(kind, {})[key] = digest
    _save(data)


def revoke(kind, key):
    """Forget any approval for kind/key; return True if there was one."""
    data = _load()
    found = data.get(kind, {}).pop(key, None) is not None
    _save(data)
    return found
