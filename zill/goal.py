"""Goals: an objective the agent keeps working toward across several turns.

Concept: some work does not fit one turn: "get the whole test suite green",
"port every module". A goal holds the objective and its status. While it is
active, Harness.pursue() starts one more turn at a time, up to a round limit,
until the model marks it complete or blocked, or the user stops it.

Design rules:
  * One goal per conversation. create_goal replaces it, update_goal changes
    its status, get_goal reads it. The user starts one with /goal.
  * Statuses: active, complete, blocked, paused. Only active goals continue;
    running out of rounds pauses the goal rather than dropping it.
  * Like the todo list, the goal is rebuilt from the transcript on resume.
"""

from .tools import tool

STATUSES = ("active", "complete", "blocked", "paused")
MAX_ROUNDS = 20
START = ("Goal: {objective}\n\nWork toward this goal over as many turns as it needs. When it "
         "is fully achieved, call update_goal with status complete. If you cannot make "
         "progress, call update_goal with status blocked and say why.")
CONTINUE = ("Continue working toward the goal: {objective}\n(round {round} of at most "
            "{rounds}). Call update_goal with status complete when it is done, or blocked "
            "if you are stuck.")


def new(objective):
    """Return a fresh active goal."""
    return {"objective": str(objective).strip(), "status": "active", "note": "", "rounds": 0}


def describe(goal):
    """Return a goal as one line."""
    if not goal:
        return "(no goal set)"
    note = f" - {goal['note']}" if goal["note"] else ""
    return f"Goal ({goal['status']}, round {goal['rounds']}): {goal['objective']}{note}"


def goal_tools(harness):
    """Return create_goal, get_goal and update_goal, bound to harness.goal."""
    @tool("Set the goal for this conversation: an objective to keep working toward across "
          "turns until it is complete.", risk="read",
          objective="What must be true when the goal is achieved")
    def create_goal(objective):
        active = harness.goal and harness.goal["status"] == "active"
        harness.goal = dict(new(objective), rounds=harness.goal["rounds"] if active else 0)
        harness.on_event("goal", dict(harness.goal))
        return f"Goal set: {harness.goal['objective']}"

    @tool("Show the current goal, its status and how many rounds it has used.", risk="read")
    def get_goal():
        return describe(harness.goal)

    @tool("Update the goal's status: complete when it is achieved, blocked when you cannot "
          "go on, paused to stop for now, active to resume.", risk="read",
          status="complete, blocked, paused or active",
          note="What was done, or what is blocking (optional)")
    def update_goal(status, note=""):
        if harness.goal is None:
            return "ERROR: no goal is set; call create_goal first"
        if status not in STATUSES:
            return f"ERROR: status must be one of {', '.join(STATUSES)}"
        harness.goal.update(status=status, note=note.strip())
        harness.on_event("goal", dict(harness.goal))
        return f"Goal is now {status}"

    return [create_goal, get_goal, update_goal]


def latest(messages):
    """Rebuild the goal from the last one set (by /goal or create_goal) and its updates, or None."""
    goal = None
    for message in messages:
        if message["role"] == "user" and message.get("goal"):
            goal = new(message["goal"])
        for call in message.get("tool_calls") or []:
            args = call.get("args") or {}
            if call["name"] == "create_goal":
                goal = new(args.get("objective", ""))
            elif call["name"] == "update_goal" and goal and args.get("status") in STATUSES:
                goal.update(status=args["status"], note=str(args.get("note", "")).strip())
    return goal
