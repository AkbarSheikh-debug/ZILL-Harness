"""Todo: a checklist the agent keeps while it works.

Concept: long builds drift. The todo tool lets the model write its plan down
as a checklist and tick items off. The harness shows the list to the user and
puts it back into context after compaction, so the plan outlives the details.

Design rules:
  * One call replaces the whole list, so there are no item ids to get wrong.
  * Lines are normalised to "- [ ] task" or "- [x] task". Anything else is an
    ERROR that shows the expected format.
  * The list is working state, not memory. It lives on the harness, and
    resume rebuilds it from the last todo call in the transcript.
"""

import re

from .tools import tool

ITEM = re.compile(r"\s*(?:[-*]\s*)?\[( |x|X)\]\s*(.+?)\s*")


def parse(items):
    """Return items as normalised checklist text, or raise ValueError."""
    lines = []
    for raw in str(items).splitlines():
        if not raw.strip():
            continue
        match = ITEM.fullmatch(raw)
        if not match:
            raise ValueError(f"not a checklist line: {raw.strip()!r}; "
                             f"use '- [ ] task' or '- [x] done task'")
        lines.append(f"- [{'x' if match.group(1) in 'xX' else ' '}] {match.group(2)}")
    return "\n".join(lines)


def todo_tool(on_update):
    """Return the todo tool; on_update(checklist) receives each accepted list."""
    @tool("Replace your task checklist. For any multi-step task, write the plan first, "
          "then resend the whole list as you finish items.", risk="read",
          items="The complete checklist, one item per line: '- [ ] task' or '- [x] done task'")
    def todo(items):
        try:
            checklist = parse(items)
        except ValueError as err:
            return f"ERROR: {err}"
        on_update(checklist)
        done = checklist.count("- [x]")
        return f"Todo list updated: {done} of {len(checklist.splitlines())} done"

    return todo


def latest(messages):
    """Return the checklist from the last valid todo call in messages, or ""."""
    for message in reversed(messages):
        for call in reversed(message.get("tool_calls") or []):
            if call["name"] == "todo":
                try:
                    return parse(call["args"].get("items", ""))
                except ValueError:
                    continue
    return ""
