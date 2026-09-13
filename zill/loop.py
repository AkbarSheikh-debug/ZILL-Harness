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
  * Every tool call carries an id and its result repeats it, so results pair
    with calls by id, never by position. Providers that send no id get one.
  * Events are plain dicts and consumers may ignore any kind: provider_start,
    text_delta (when streaming), provider_end, assistant, tool_start,
    tool_blocked, tool_end.
"""

import time
import uuid

from . import provider


def run_loop(model, system, messages, tools, on_event, before_tool,
             max_turns=80, before_turn=None, after_tool=None, stream=False):
    """Drive the model until it answers without tool calls; return that text.

    tools maps name -> Tool (with .spec and .run). on_event(kind, payload)
    reports progress. before_tool(call) returns None to allow a call or a
    reason string to block it. after_tool(call, result) may extend the
    result of a call that ran. With stream, text arrives as text_delta events
    and the assistant event is marked streamed.
    """
    specs = [t.spec for t in tools.values()]
    for _ in range(max_turns):
        if before_turn is not None:
            # Replace contents, not the binding, so the caller's list stays live.
            messages[:] = before_turn(messages)
        reply = _call_model(model, system, messages, specs, on_event, stream)
        if not reply["tool_calls"]:
            return reply["text"]
        for call in reply["tool_calls"]:
            on_event("tool_start", call)
            started = time.monotonic()
            reason = before_tool(call)
            if reason is not None:
                result = f"BLOCKED: {reason}"
                on_event("tool_blocked", {"id": call["id"], "name": call["name"],
                                          "reason": reason})
            else:
                result = _execute(call, tools)
                if after_tool is not None:
                    result = after_tool(call, result)
            on_event("tool_end", {"id": call["id"], "name": call["name"], "result": result,
                                  "seconds": round(time.monotonic() - started, 3)})
            messages.append({"role": "tool", "id": call["id"], "name": call["name"],
                             "text": result})

    messages.append({"role": "user", "text": "Turn limit reached; wrap up now."})
    return _call_model(model, system, messages, [], on_event, stream)["text"]


def _call_model(model, system, messages, specs, on_event, stream=False):
    """Make one model call, record the assistant message, and return the reply."""
    on_event("provider_start", {"model": model, "messages": len(messages)})
    on_text = (lambda chunk: on_event("text_delta", {"text": chunk})) if stream else None
    reply = provider.complete(model, system, messages, specs, on_text=on_text)
    reply["streamed"] = stream
    on_event("provider_end", {"model": model, "usage": reply.get("usage") or {}})
    for call in reply["tool_calls"]:
        call["id"] = call.get("id") or f"call_{uuid.uuid4().hex[:12]}"
    message = {"role": "assistant", "text": reply["text"], "tool_calls": reply["tool_calls"]}
    if reply.get("provider_data"):  # adapter-private, replayed untouched
        message["provider_data"] = reply["provider_data"]
    messages.append(message)
    on_event("assistant", reply)
    return reply


def _execute(call, tools):
    """Run one allowed tool call and return its result as a string, never raising."""
    tool = tools.get(call["name"])
    if tool is None:
        return f"ERROR: unknown tool {call['name']}"
    try:
        return str(tool.run(**call["args"]))
    except Exception as err:  # the model sees the failure; the loop survives
        return f"ERROR: {type(err).__name__}: {err}"
