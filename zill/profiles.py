"""Profiles: named presets that point one harness at one kind of work.

Concept: reviewing code, researching a question and writing a document need
different instructions, tools and policy than building software. A profile
composes existing pieces (a system prompt addition, a tool allowlist and a
strictest mode) and adds no behaviour of its own to the loop.

Design rules:
  * A profile can only narrow: fewer tools, a stricter mode. It never grants
    anything a plain run would not have.
  * "tools" lists the tools kept (None keeps all); tools passed in code
    through extra_tools are always kept.
"""

READS = ["read_file", "list_files", "grep", "todo", "use_skill", "spawn_agent"]

PROFILES = {
    "coding": {"prompt": "", "tools": None, "mode": None},
    "reviewer": {
        "prompt": ("Profile: reviewer. You review; you do not change anything. Read the "
                   "relevant code, then report real bugs, risks and unclear parts, each with "
                   "its file and line and a concrete suggested fix. Say plainly when you "
                   "found nothing serious."),
        "tools": READS, "mode": "read-only"},
    "research": {
        "prompt": ("Profile: research. Investigate and explain. Read broadly before "
                   "concluding, cite the file and line behind every claim, and say plainly "
                   "what you could not confirm."),
        "tools": READS + ["remember"], "mode": "read-only"},
    "writing": {
        "prompt": ("Profile: writing. You write and edit prose documents such as READMEs, "
                   "guides and notes. Match the audience and the existing voice, keep "
                   "structure clear, and do not change source code."),
        "tools": READS + ["write_file", "edit_file", "remember"], "mode": None},
}
