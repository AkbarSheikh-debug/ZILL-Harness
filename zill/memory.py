"""Day 3 — Memory: a system prompt that survives between conversations.

Concept: a conversation ends and its messages are gone, but the working
directory stays. Durable memory is therefore just a file in that directory,
ZILL.md, read back into the system prompt at the start of every run. The
same file is where remember() appends the facts worth keeping.

Design rules:
  * Plain Markdown the user can read and edit by hand; no hidden database.
  * build_system_prompt is deterministic: the same directory contents give
    the same prompt, so a fresh run starts exactly where the file says.
  * The prompt names the real platform and path, so commands and paths the
    model writes match the machine it is running on.
"""

import os
import platform

MEMORY_FILE = "ZILL.md"
SHELL = "cmd.exe" if platform.system() == "Windows" else "/bin/sh"
BASE_PROMPT = """You are ZILL, a small, sharp coding agent. You work inside one \
directory using only the tools provided.
- Act, don't narrate: call tools rather than describing what you would do.
- Inspect before assuming: read files and list directories before changing them.
- Prefer edit_file for small changes; use write_file for new files or full rewrites.
- Verify after building: run the code or re-read the file to confirm it is right.
- Never repeat a failing call unchanged; read the error and adjust.
- When the task is complete, reply with a short summary and stop calling tools."""


def build_system_prompt(workdir, extra=""):
    """Compose the base prompt, environment line, project memory, and extra."""
    root = os.path.realpath(workdir)
    sections = [
        BASE_PROMPT,
        f"Platform: {platform.system()} (bash tool runs through {SHELL}). "
        f"Working directory: {root}",
    ]
    path = os.path.join(root, MEMORY_FILE)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            sections.append(f"Project memory ({MEMORY_FILE}):\n{f.read().strip()}")
    if extra:
        sections.append(extra)
    return "\n\n".join(sections)


def remember(workdir, note):
    """Append note as a bullet to the project memory file."""
    with open(os.path.join(workdir, MEMORY_FILE), "a", encoding="utf-8") as f:
        f.write(f"- {note}\n")
    return f"Remembered in {MEMORY_FILE}"
