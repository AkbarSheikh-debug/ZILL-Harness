#!/bin/sh
# Install ZILL: curl -fsSL https://raw.githubusercontent.com/AkbarSheikh-debug/ZILL-Harness/main/install.sh | sh
# Uses uv if present, then pipx, then pip --user; with no Python 3.10+, it installs uv, which brings its own Python.
# Set ZILL_SOURCE to install something else (e.g. zill-harness from PyPI, or a local path).
# The browser app (zill ui) comes along from ZILL_UI_SOURCE; set it empty to skip the app.
set -eu

SOURCE="${ZILL_SOURCE:-https://github.com/AkbarSheikh-debug/ZILL-Harness/archive/refs/heads/main.zip}"
UI_SOURCE="${ZILL_UI_SOURCE-https://github.com/AkbarSheikh-debug/ZILL-UI/archive/refs/heads/main.zip}"
NO_UI="the app (zill ui) could not be installed; ZILL works without it (see the README to add it later)"

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

uv_install() {  # uv_install [--python X]: ZILL with the app, or without it if the app fails
    # uv's own Python, not whatever is on PATH (a conda base Python can break HTTPS).
    if [ -n "$UI_SOURCE" ] && uv tool install --force --managed-python "$@" "$SOURCE" --with "$UI_SOURCE"; then
        return 0
    fi
    [ -z "$UI_SOURCE" ] || say "$NO_UI"
    uv tool install --force --managed-python "$@" "$SOURCE"
}

pip_install() {  # pip_install PY: ZILL with the app, or without it if the app fails
    for pkgs in "$SOURCE $UI_SOURCE" "$SOURCE"; do
        # shellcheck disable=SC2086  # the package list splits on purpose
        if "$1" -m pip install --user --upgrade $pkgs 2>/dev/null \
            || "$1" -m pip install --user --upgrade --break-system-packages $pkgs; then
            [ "$pkgs" = "$SOURCE" ] && [ -n "$UI_SOURCE" ] && say "$NO_UI"
            return 0
        fi
    done
    return 1
}

if has uv; then
    say "installing with uv"
    uv_install
    uv tool update-shell >/dev/null 2>&1 || true
elif has pipx; then
    say "installing with pipx"
    pipx install --force "$SOURCE"
    if [ -n "$UI_SOURCE" ] && ! pipx inject --force zill-harness "$UI_SOURCE"; then
        say "$NO_UI"
    fi
    pipx ensurepath >/dev/null 2>&1 || true
elif PY=$(find_python) && "$PY" -m pip --version >/dev/null 2>&1; then
    say "installing with $PY -m pip --user"
    pip_install "$PY"
    BIN=$("$PY" -c 'import sysconfig; print(sysconfig.get_path("scripts", "posix_user"))')
    case ":$PATH:" in *":$BIN:"*) ;; *) say "add $BIN to your PATH" ;; esac
else
    say "no Python 3.10+ found; installing uv (it brings its own Python)"
    has curl || { say "curl is required"; exit 1; }
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    uv_install --python 3.12
    uv tool update-shell >/dev/null 2>&1 || true
fi

say "done. Open a new terminal and run: zill    (or zill ui for the app)"
