"""Credentials: API keys from the environment or one local file, never shown.

Concept: a user pastes a key once (`zill setup`) and every later run finds
it. Lookups read like environment variables, so a key can live in the shell,
in CI secrets, or in ~/.zill/credentials.json, and the code does not care.

Design rules:
  * The environment always wins; the file is only a fallback.
  * The file is written atomically with owner-only permissions (0600).
  * redact() replaces every known secret value before text is displayed, so
    a key echoed by a tool or an error never reaches the terminal.
  * A corrupt file is a clear error naming the file, never a silent {}.
"""

import json
import os

SECRET_SUFFIXES = ("_KEY", "_TOKEN")
MIN_SECRET_CHARS = 8  # shorter values are not keys, and redacting them garbles text
REDACTED = "[REDACTED]"


def home():
    """Return ZILL's user directory: ZILL_HOME, or ~/.zill."""
    return os.environ.get("ZILL_HOME") or os.path.join(os.path.expanduser("~"), ".zill")


def path():
    """Return the credentials file path."""
    return os.path.join(home(), "credentials.json")


def load():
    """Return the stored credentials as a dict ({} when there is no file)."""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise RuntimeError(f"cannot read {path()}: {err}") from err
    if not isinstance(data, dict):
        raise RuntimeError(f"{path()} must contain a JSON object")
    return data


def get(name):
    """Return the value of name from the environment, else the file, else None."""
    return os.environ.get(name) or load().get(name)


def save(name, value):
    """Store name=value in the credentials file, readable only by its owner."""
    data = load()
    data[name] = value
    os.makedirs(home(), exist_ok=True)
    temp = f"{path()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, path())


def secrets():
    """Return every secret value known: *_KEY and *_TOKEN from file and environment."""
    pairs = list(load().items()) + list(os.environ.items())
    return {str(value) for name, value in pairs
            if name.upper().endswith(SECRET_SUFFIXES) and len(str(value)) >= MIN_SECRET_CHARS}


def redact(text):
    """Return text with every known secret value replaced by [REDACTED]."""
    for value in sorted(secrets(), key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text
