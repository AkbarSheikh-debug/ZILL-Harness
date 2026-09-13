"""Day 3 — Skills: instructions the agent loads only when a task needs them.

Concept: a skill is a folder, skills/<name>/SKILL.md, holding instructions
for one kind of work. The system prompt carries only a one-line catalog;
the model pulls a skill's full text through the use_skill tool when it
decides the skill is relevant. New behaviour ships as a file, not as code.

Design rules:
  * The directory name is the skill's name; a "description:" line in the
    file's front matter is its catalog entry.
  * Only the catalog costs context up front; full text arrives on demand.
  * Lookups go through catalog(), so a model-supplied name can only ever
    select an existing SKILL.md, never an arbitrary path.
"""

import os

from .tools import tool

SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"


def catalog(workdir):
    """Return {name: {"description", "path"}} for every skill in workdir."""
    base = os.path.join(workdir, SKILLS_DIR)
    if not os.path.isdir(base):
        return {}
    skills = {}
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, SKILL_FILE)
        if os.path.isfile(path):
            skills[name] = {"description": _description(path), "path": path}
    return skills


def catalog_prompt(workdir):
    """Render the skill catalog as a system prompt section, or ""."""
    skills = catalog(workdir)
    if not skills:
        return ""
    lines = ["Skills available (load one with the use_skill tool when relevant):"]
    lines += [f"- {name}: {info['description']}" for name, info in skills.items()]
    return "\n".join(lines)


def read_skill(workdir, name):
    """Return the full SKILL.md text for name, or an ERROR listing the options."""
    skills = catalog(workdir)
    if name not in skills:
        available = ", ".join(skills) or "(none)"
        return f"ERROR: no skill named {name}. Available: {available}"
    with open(skills[name]["path"], encoding="utf-8") as f:
        return f.read()


def skill_tools(workdir):
    """Return the use_skill tool, bound to workdir's skills."""
    @tool("Load the full instructions of a skill from the catalog in the system prompt.",
          name="Skill name exactly as listed in the catalog")
    def use_skill(name):
        return read_skill(workdir, name)

    return [use_skill]


def _description(path):
    """Read "description:" from the front matter block, or return ""."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        if key.strip() == "description":
            return value.strip().strip("\"'")
    return ""
