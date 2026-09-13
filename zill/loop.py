"""Day 1 — The agent loop: call the model, run its tools, repeat.

Concept: an agent is a while-loop around a model. Each turn the model either
answers (and the loop ends) or asks for tools, whose results are appended to
the conversation before the next call.

Design rules:
  * The message list is the only state; the loop mutates it in place so the
    caller keeps the full transcript.
  * The loop never crashes because a tool did. Unknown tools and exceptions
    become "ERROR: ..." results the model can read and recover from.
  * Policy lives outside: before_tool decides what may run, on_event decides
    what gets shown, before_turn (day 3) decides what the model sees.
  * Every run terminates: after max_turns the model is told to wrap up and
    gets one last call with no tools.
"""

from . import provider


def run_loop(model, system, messages, tools, on_event, before_tool,
             max_turns=80, before_turn=None):
    """Drive the model until it answers without tool calls; return that text.

    tools maps name -> Tool (with .spec and .run). on_event(kind, payload)
    fires "assistant", "tool_start" and "tool_end". before_tool(call) returns
    None to allow a call or a reason string to block it.
    """
    specs = [t.spec for t in tools.values()]
    for _ in range(max_turns):
        if before_turn is not None:
            # Replace contents, not the binding, so the caller's list stays live.
            messages[:] = before_turn(messages)
        reply = provider.complete(model, system, messages, specs)
        messages.append({"role": "assistant", "text": reply["text"],
                         "tool_calls": reply["tool_calls"]})
        on_event("assistant", reply)
        if not reply["tool_calls"]:
            return reply["text"]
        for call in reply["tool_calls"]:
            on_event("tool_start", call)
            result = _execute(call, tools, before_tool)
            on_event("tool_end", {"name": call["name"], "result": result})
            messages.append({"role": "tool", "name": call["name"], "text": result})

    messages.append({"role": "user", "text": "Turn limit reached; wrap up now."})
    reply = provider.complete(model, system, messages, [])
    messages.append({"role": "assistant", "text": reply["text"], "tool_calls": []})
    on_event("assistant", reply)
    return reply["text"]


def _execute(call, tools, before_tool):
    """Run one tool call and return its result as a string, never raising."""
    reason = before_tool(call)
    if reason is not None:
        return f"BLOCKED: {reason}"
    tool = tools.get(call["name"])
    if tool is None:
        return f"ERROR: unknown tool {call['name']}"
    try:
        return str(tool.run(**call["args"]))
    except Exception as err:  # the model sees the failure; the loop survives
        return f"ERROR: {type(err).__name__}: {err}"
