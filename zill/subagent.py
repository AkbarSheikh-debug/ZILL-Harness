"""Day 4 — Sub-agents: hand a self-contained task to a fresh conversation.

Concept: a long task pollutes one context with every file read and every
failed attempt. spawn_agent starts a child harness with an empty transcript,
gives it only the task text, and returns only its final report. The parent
pays for one tool result, not for the child's whole journey.

Design rules:
  * The child sees nothing of the parent's conversation; the task string must
    carry everything it needs.
  * Recursion is bounded: at max_depth the tool refuses and tells the model
    to do the work itself, so delegation can never loop forever.
  * The factory is injected. subagent.py knows nothing about how a harness is
    built, which keeps it free of a circular import with harness.py.
"""

from .tools import tool

DEPTH_LIMIT = "ERROR: sub-agent depth limit reached; do this task yourself"


def subagent_tool(make_harness, depth=0, max_depth=2):
    """Return the spawn_agent tool; make_harness(depth) builds each child."""
    @tool("Delegate a self-contained task to a fresh sub-agent with its own clean "
          "context. The child cannot see this conversation, so the task must "
          "include every path, requirement and detail it needs. Returns the "
          "child's final report.",
          task="Complete, standalone instructions for the sub-agent")
    def spawn_agent(task):
        if depth >= max_depth:
            return DEPTH_LIMIT
        child = make_harness(depth + 1)
        return child.run(task)

    return spawn_agent
