#!/bin/sh
# Install ZILL: curl -fsSL https://raw.githubusercontent.com/AkbarSheikh-debug/ZILL-Harness/main/install.sh | sh
# Uses uv if present, then pipx, then pip --user; with no Python 3.10+, it installs uv, which brings its own Python.
# Set ZILL_SOURCE to install something else (e.g. zill-harness from PyPI, or a local path).
set -eu

SOURCE="${ZILL_SOURCE:-https://github.com/AkbarSheikh-debug/ZILL-Harness/archive/refs/heads/main.zip}"

say() { printf '\033[1mzill:\033[0m %s\n' "$*"; }
has() { command -v "$1" >/dev/null 2>&1; }

find_python() {
    for py in python3 python; do
        # ssl too: ZILL needs HTTPS, and a Python run outside its conda environment may lack it.
        if has "$py" && "$py" -c 'import sys, ssl; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            echo "$py"; return 0
        fi
    done
    return 1
}

if has uv; then
    say "installing with uv"
    # uv's own Python, not whatever is on PATH (a conda base Python can break HTTPS).
    uv tool install --force --managed-python "$SOURCE"
    uv tool update-shell >/dev/null 2>&1 || true
elif has pipx; then
    say "installing with pipx"
    pipx install --force "$SOURCE"
    pipx ensurepath >/dev/null 2>&1 || true
elif PY=$(find_python) && "$PY" -m pip --version >/dev/null 2>&1; then
    say "installing with $PY -m pip --user"
    "$PY" -m pip install --user --upgrade "$SOURCE" 2>/dev/null \
        || "$PY" -m pip install --user --upgrade --break-system-packages "$SOURCE"
    BIN=$("$PY" -c 'import sysconfig; print(sysconfig.get_path("scripts", "posix_user"))')
    case ":$PATH:" in *":$BIN:"*) ;; *) say "add $BIN to your PATH" ;; esac
else
    say "no Python 3.10+ found; installing uv (it brings its own Python)"
    has curl || { say "curl is required"; exit 1; }
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    uv tool install --force --managed-python --python 3.12 "$SOURCE"
    uv tool update-shell >/dev/null 2>&1 || true
fi

say "done. Open a new terminal and run: zill"
