"""Anthropic adapter: neutral messages <-> the Claude Messages API.

Concept: Claude speaks in content blocks. An assistant turn is a list of
thinking, text and tool_use blocks; tool results go back as tool_result
blocks inside a user turn, matched to their call by tool_use_id.

Design rules:
  * The raw content blocks of every reply are kept as provider_data and
    replayed verbatim: the API rejects thinking blocks that were modified or
    rebuilt, so they are never reconstructed from text and tool_calls.
  * Consecutive tool results share one user turn, so parallel calls stay
    parallel in the model's eyes.
  * The system prompt carries a cache breakpoint and the request enables
    automatic caching, so each turn re-reads the growing history from cache.
  * Each model's default thinking is kept, and no sampling parameters are
    sent, since current models reject them.
  * An effort goes out as output_config.effort. Models without the control get
    none, and a level a model lacks steps down to the nearest one it has.
"""

import json

from .http import post_json, post_sse

# Stream deltas and the block field each one extends.
DELTA_FIELDS = {"text_delta": "text", "thinking_delta": "thinking",
                "signature_delta": "signature", "input_json_delta": "partial_json"}
VERSION = "2023-06-01"
MAX_TOKENS = 16000  # non-streaming requests stay well under HTTP timeouts
WINDOWS = {"claude-haiku-4-5": 200_000}  # every other current model: 1M
CACHE = {"type": "ephemeral"}
REFUSED = "(The model declined this request.)"
NO_EFFORT = ("claude-3", "claude-haiku", "claude-sonnet-4-5", "claude-sonnet-4-2",
             "claude-opus-4-1", "claude-opus-4-2")  # these reject output_config.effort
# model prefix: the levels it lacks, and what each steps down to
STEP_DOWN = {"claude-opus-4-5": {"xhigh": "high", "max": "high"},
             "claude-opus-4-6": {"xhigh": "high"}, "claude-sonnet-4-6": {"xhigh": "high"}}


def auth_headers(key):
    """Return the headers that authenticate key."""
    return {"x-api-key": key, "anthropic-version": VERSION}


def model_info(model):
    """Return what model supports: tools, parallel calls, window and output sizes."""
    return {"supports_tools": True, "supports_parallel_tools": True,
            "supports_thought_signatures": False,
            "context_window": WINDOWS.get(model, 1_000_000), "max_output_tokens": MAX_TOKENS}


def _to_wire(messages):
    """Translate neutral messages into Messages API turns."""
    wire = []

    def add(role, blocks):
        if wire and wire[-1]["role"] == role:
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": role, "content": list(blocks)})

    for m in messages:
        if m["role"] == "user":
            add("user", [{"type": "text", "text": m["text"] or "(empty)"}])
        elif m["role"] == "assistant":
            blocks = (m.get("provider_data") or {}).get("anthropic")
            if not blocks:  # written by another provider: rebuild what we can
                blocks = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"],
                            "input": c["args"]} for c in m.get("tool_calls") or []]
            add("assistant", blocks or [{"type": "text", "text": "(no reply)"}])
        elif m["role"] == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": m["id"],
                          "content": m["text"] or "(no output)"}])
        else:
            raise ValueError(f"unknown message role: {m['role']!r}")
    return wire


def _collect_stream(events, on_text):
    """Rebuild a Messages API response from its stream events."""
    blocks, usage, stop_reason = {}, {}, None
    for event in events:
        kind = event.get("type")
        if kind == "message_start":
            usage.update(event["message"].get("usage") or {})
        elif kind == "content_block_start":
            blocks[event["index"]] = dict(event["content_block"])
        elif kind == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            field = DELTA_FIELDS.get(delta.get("type"))
            if field:  # unknown delta kinds (citations, ...) change no replayed field
                block[field] = block.get(field, "") + delta[field]
                if field == "text":
                    on_text(delta["text"])
        elif kind == "content_block_stop":
            block = blocks[event["index"]]
            if "partial_json" in block:
                block["input"] = json.loads(block.pop("partial_json") or "{}")
        elif kind == "message_delta":
            stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
            usage.update(event.get("usage") or {})
        elif kind == "error":
            raise RuntimeError(f"Anthropic stream error: {event.get('error')}")
    return {"content": [blocks[i] for i in sorted(blocks)], "stop_reason": stop_reason,
            "usage": usage}


def effort_for(model, effort):
    """Return the output_config effort model accepts for effort, or None to send none."""
    if not effort or model.startswith(NO_EFFORT):
        return None
    for prefix, lower in STEP_DOWN.items():
        if model.startswith(prefix):
            return lower.get(effort, effort)
    return effort


def complete(model, system, messages, tools, base, key, on_text=None, effort=None):
    """Send one conversation to Claude and return its neutral reply.

    With on_text, the reply is streamed, text fragments go to on_text as
    they arrive, and the content blocks are rebuilt exactly from the stream.
    """
    body = {"model": model, "max_tokens": MAX_TOKENS, "cache_control": CACHE,
            "system": [{"type": "text", "text": system, "cache_control": CACHE}],
            "messages": _to_wire(messages)}
    level = effort_for(model, effort)
    if level:
        body["output_config"] = {"effort": level}
    if tools:
        body["tools"] = [{"name": s["name"], "description": s["description"],
                          "input_schema": s["parameters"]}
                         for s in (t["schema"] for t in tools)]
    headers = auth_headers(key)
    if on_text is None:
        data = post_json(f"{base}/messages", body, headers, "Anthropic")
    else:
        data = _collect_stream(post_sse(f"{base}/messages", {**body, "stream": True},
                                        headers, "Anthropic"), on_text)

    blocks = data.get("content") or []
    text = "".join(b["text"] for b in blocks if b.get("type") == "text")
    calls = [{"id": b["id"], "name": b["name"], "args": b.get("input") or {}}
             for b in blocks if b.get("type") == "tool_use"]
    if data.get("stop_reason") == "refusal" and not text:
        text = REFUSED
    usage = data.get("usage") or {}
    read = sum(usage.get(k) or 0 for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {"text": text, "tool_calls": calls, "provider_data": {"anthropic": blocks},
            "usage": {"input": read, "output": usage.get("output_tokens", 0)}}
