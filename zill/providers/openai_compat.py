"""OpenAI-compatible adapter: neutral messages <-> /chat/completions.

Concept: one wire format is spoken by OpenAI, OpenRouter, Groq, DeepSeek and
local servers such as Ollama and LM Studio. Only the base URL and the key
differ, so one adapter serves them all.

Design rules:
  * Tool arguments arrive as a JSON string. A string that does not parse
    becomes empty arguments, so the tool reports what is missing instead of
    the loop crashing.
  * No max_tokens or temperature: servers disagree on those parameter names,
    so each server's own defaults apply.
  * The key is optional, because local servers do not need one.
"""

import json

from .http import post_json

MODEL_INFO = {"supports_tools": True, "supports_parallel_tools": True,
              "supports_thought_signatures": False, "context_window": 128_000,
              "max_output_tokens": 16_000}  # conservative: servers vary widely


def model_info(model):
    """Return what model supports: tools, parallel calls, window and output sizes."""
    return dict(MODEL_INFO)


def _to_wire(messages):
    """Translate neutral messages into chat-completions messages."""
    wire = []
    for m in messages:
        if m["role"] == "user":
            wire.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            message = {"role": "assistant", "content": m.get("text") or None}
            calls = m.get("tool_calls") or []
            if calls:  # some servers reject an empty tool_calls list
                message["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    for c in calls]
            wire.append(message)
        elif m["role"] == "tool":
            wire.append({"role": "tool", "tool_call_id": m["id"], "content": m["text"]})
        else:
            raise ValueError(f"unknown message role: {m['role']!r}")
    return wire


def _arguments(raw):
    """Decode a tool call's JSON argument string; anything unparseable is {}."""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


def complete(model, system, messages, tools, base, key):
    """Send one conversation to an OpenAI-compatible server and return its reply."""
    body = {"model": model,
            "messages": [{"role": "system", "content": system}] + _to_wire(messages)}
    if tools:
        body["tools"] = [{"type": "function", "function": t["schema"]} for t in tools]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = post_json(f"{base}/chat/completions", body, headers, "OpenAI-compatible")

    message = ((data.get("choices") or [{}])[0].get("message")) or {}
    calls = [{"id": c.get("id"), "name": (c.get("function") or {}).get("name", ""),
              "args": _arguments((c.get("function") or {}).get("arguments"))}
             for c in message.get("tool_calls") or []]
    usage = data.get("usage") or {}
    return {"text": message.get("content") or "", "tool_calls": calls,
            "usage": {"input": usage.get("prompt_tokens", 0),
                      "output": usage.get("completion_tokens", 0)}}
