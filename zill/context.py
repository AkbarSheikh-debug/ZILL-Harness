"""Day 3 — The context engine: keep the conversation inside a token budget.

Concept: the model only sees what fits in its window, and a long tool-driven
run outgrows any window. Before each turn, compact() checks the transcript
against a budget; when it is over, the older messages are summarised by the
model itself into one dense note and only the most recent exchange is kept
verbatim. The loop's before_turn socket is where this plugs in.

Design rules:
  * Cheap estimate, no tokenizer: characters divided by CHARS_PER_TOKEN is
    close enough to decide when to act, and costs nothing to compute.
  * Under budget, compact() returns the very same list — no copy, no call.
  * The last KEEP_RECENT messages survive word for word, so the model never
    loses the exact state of the step it is in the middle of.
  * A kept slice never opens with a tool result whose call was summarised
    away; those results move into the summarised part instead of vanishing.
"""

import json

from . import provider

CHARS_PER_TOKEN = 4
KEEP_RECENT = 6
CLIP_CHARS = 600  # per-message text kept in the transcript sent for summary
CLIP_ARGS = 200  # per-call argument preview kept in that transcript
HEADER = "[Conversation so far, compacted]"
SUMMARY_SYSTEM = (
    "You compress agent transcripts. Preserve: the original task, every file "
    "created or edited and its purpose, key decisions, unresolved errors, and "
    "what remains to be done. Be dense and factual."
)


def estimate_tokens(messages):
    """Approximate the token count of messages from their printed length."""
    return sum(len(str(m)) for m in messages) // CHARS_PER_TOKEN


def compact(model, messages, budget_tokens):
    """Return messages, or a summary plus the recent tail if over budget."""
    if estimate_tokens(messages) <= budget_tokens or len(messages) <= KEEP_RECENT + 1:
        return messages
    old, recent = messages[:-KEEP_RECENT], messages[-KEEP_RECENT:]
    # A leading tool result would answer a call the model can no longer see.
    while recent and recent[0]["role"] == "tool":
        old, recent = old + recent[:1], recent[1:]
    request = [{"role": "user", "text": _render(old)}]
    summary = provider.complete(model, SUMMARY_SYSTEM, request, [])["text"]
    return [{"role": "user", "text": f"{HEADER}\n{summary}"}] + recent


def _render(messages):
    """Flatten messages into a plain-text transcript for the summariser."""
    lines = []
    for m in messages:
        label = f"{m['role']}({m['name']})" if m.get("name") else m["role"]
        text = m.get("text") or ""
        if len(text) > CLIP_CHARS:
            text = f"{text[:CLIP_CHARS]} ... [{len(text) - CLIP_CHARS} chars clipped]"
        lines.append(f"{label}: {text}")
        for call in m.get("tool_calls") or []:
            args = json.dumps(call["args"])[:CLIP_ARGS]
            lines.append(f"  -> called {call['name']} {args}")
    return "\n".join(lines)
