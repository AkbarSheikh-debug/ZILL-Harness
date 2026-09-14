"""Interaction: tools that pause for the person, or hand work over to them.

Concept: an agent that can only act must guess when a choice belongs to the
user. ask_user_question asks and waits. exit_plan_mode ends plan mode by
putting a plan in front of the user for approval. present hands finished
files to the user, so the terminal lists them and the app shows them as cards.

Design rules:
  * The harness's asker answers, whoever is watching: the terminal prompts, the
    app shows a card. With no asker (a headless run) the tools say so, and the
    model proceeds on its own judgement instead of waiting forever.
  * Plan mode allows reads only; Policy blocks everything else. Only the
    user's approval, through exit_plan_mode, turns it off.
  * present accepts only existing files inside the working directory.
"""

import os
import re

from .tools import tool

PLAN_NOTE = ("Plan mode is on until exit_plan_mode reports that the user approved your "
             "plan. Investigate with read-only tools, then call exit_plan_mode with a concise, "
             "numbered plan. Changing files and running commands is blocked until then.")
NO_ANSWER = ("The user did not answer (no one is available, or they dismissed the question). "
             "Proceed on your best judgement and say what you assumed.")


def interaction_tools(harness):
    """Return ask_user_question, exit_plan_mode and present, bound to harness."""
    @tool("Ask the user a question and wait for the answer. Use it when a decision is "
          "genuinely theirs (a preference, a trade-off, missing information), not for "
          "anything you can find out yourself.", risk="read",
          question="The question, as one clear sentence",
          options="Choices, one per line as 'label: short description' (optional; without "
                  "options the user types an answer)",
          multi_select="'true' to let the user pick several options (default false)",
          header="A two- or three-word label for the question (optional)")
    def ask_user_question(question, options="", multi_select="false", header=""):
        choices = [dict(zip(("label", "description"), (p.strip() for p in line.split(":", 1))))
                   for line in options.splitlines() if line.strip()]
        answer = harness.ask({"kind": "question", "question": question, "header": header,
                              "options": choices,
                              "multi_select": str(multi_select).lower() == "true"})
        return NO_ANSWER if answer in (None, "") else f"The user answered: {answer}"

    @tool("Finish planning: show the user your plan and ask to approve it. Call it only in "
          "plan mode, once you have investigated enough to plan well.", risk="read",
          plan="The plan, as short numbered steps")
    def exit_plan_mode(plan):
        if not harness.policy.plan:
            return "Plan mode is not on; carry on with the task."
        answer = harness.ask({"kind": "plan", "plan": plan})
        if answer is None:
            return ("No one is available to review the plan, so plan mode stays on. "
                    "Give the plan as your final answer and stop.")
        if answer.get("approved"):
            harness.policy.plan = False
            harness.on_event("plan", {"on": False, "plan": plan})
            return "The user approved the plan. Plan mode is off: carry the plan out now."
        return (f"The user did not approve the plan. Their feedback: "
                f"{answer.get('feedback') or '(none given)'}. Revise it and call "
                f"exit_plan_mode again.")

    @tool("Hand finished files to the user, such as a report, a generated document or the "
          "main file you built. They are listed for the user to open.", risk="read",
          paths="File paths relative to the working directory, one per line or comma-separated",
          note="One line about what the files are (optional)")
    def present(paths, note=""):
        files = []
        for rel in filter(None, (p.strip() for p in re.split(r"[\n,]", paths))):
            full = os.path.realpath(os.path.join(harness.workdir, rel))
            try:
                inside = os.path.commonpath([harness.workdir, full]) == harness.workdir
            except ValueError:
                inside = False
            if not inside or not os.path.isfile(full):
                return f"ERROR: {rel} is not a file inside the working directory"
            files.append({"path": os.path.relpath(full, harness.workdir).replace(os.sep, "/"),
                          "bytes": os.path.getsize(full), "note": note})
        if not files:
            return "ERROR: name at least one file"
        shown = {f["path"] for f in files}
        harness.presented = [f for f in harness.presented if f["path"] not in shown] + files
        harness.on_event("present", {"files": files, "note": note})
        return f"Presented to the user: {', '.join(sorted(shown))}"

    return [ask_user_question, exit_plan_mode, present]
