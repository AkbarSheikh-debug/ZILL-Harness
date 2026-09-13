"""Example connector: a notes "service" that lives in the project's notes/ folder.

A connector is a plugin that integrates a data source. This one needs no
account, so it runs anywhere. A real connector (GitHub, a database, a mail
provider) has the same shape; it also:
  * lists the credentials it needs in manifest.json, for example
    "credentials": ["GITHUB_TOKEN"];
  * reads them with registry.credential("GITHUB_TOKEN") inside its tools;
  * declares "network" in its permissions if it calls a remote API.
If a declared credential is missing, ZILL still loads the connector, and each
tool answers "ERROR: <name> connector is not configured" until it is set.

Try it:
    mkdir -p .zill/plugins && cp -r examples/connectors/local_notes .zill/plugins/local_notes
    zill plugin enable local_notes
    zill run "create a note called ideas with three ideas for this project"
"""

import os
import re

from zill import tool

SAFE_NAME = re.compile(r"[\w-]{1,64}")


def register(registry):
    folder = os.path.join(registry.workdir, "notes")

    def path_for(name):
        if not SAFE_NAME.fullmatch(name):
            raise ValueError("note names use letters, digits, _ and - only")
        return os.path.join(folder, f"{name}.md")

    @tool("List the notes in the project's notes folder.", risk="read")
    def list_notes():
        if not os.path.isdir(folder):
            return "(no notes yet)"
        names = sorted(f[:-3] for f in os.listdir(folder) if f.endswith(".md"))
        return "\n".join(names) or "(no notes yet)"

    @tool("Read one note.", risk="read", name="Note name, without .md")
    def read_note(name):
        with open(path_for(name), encoding="utf-8") as f:
            return f.read()

    @tool("Create or replace a note.", risk="write", name="Note name, without .md",
          text="The note's Markdown content")
    def create_note(name, text):
        os.makedirs(folder, exist_ok=True)
        with open(path_for(name), "w", encoding="utf-8") as f:
            f.write(text)
        return f"saved note {name}"

    for each in (list_notes, read_note, create_note):
        registry.add_tool(each)
