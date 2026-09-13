"""Gemini adapter: neutral messages <-> the Gemini generateContent API.

Concept: this module turns the harness's neutral message format into
Gemini's wire format, makes one HTTP call, and turns the answer back into
a plain dict. It is the only code that knows what a "part" is.

Design rules:
  * Neutral in, neutral out: only _to_wire and complete() see Gemini JSON.
  * Gemini 3 rejects a follow-up request unless each functionCall part carries
    back the exact thoughtSignature it arrived with, so calls keep it.
  * When Gemini issues its own call id, the functionCall and its
    functionResponse both echo it; ids ZILL invented are never sent.
"""

from .http import post_json

MODEL_INFO = {"supports_tools": True, "supports_parallel_tools": True,
              "supports_thought_signatures": True, "context_window": 1_048_576,
              "max_output_tokens": 65536}


def model_info(model):
    """Return what model supports: tools, parallel calls, window and output sizes."""
    return dict(MODEL_INFO)


def _to_wire(messages):
    """Translate neutral messages into Gemini `contents` entries."""
    contents, native_ids = [], set()
    for m in messages:
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["text"]}]})
        elif m["role"] == "assistant":
            parts = [{"text": m["text"]}] if m.get("text") else []
            for call in m.get("tool_calls") or []:
                function_call = {"name": call["name"], "args": call["args"]}
                if call.get("native_id"):
                    function_call["id"] = call["native_id"]
                    native_ids.add(call["id"])
                part = {"functionCall": function_call}
                if call.get("signature"):
                    part["thoughtSignature"] = call["signature"]
                parts.append(part)
            contents.append({"role": "model", "parts": parts})
        elif m["role"] == "tool":
            response = {"name": m["name"], "response": {"result": m["text"]}}
            if m.get("id") in native_ids:
                response["id"] = m["id"]
            contents.append({"role": "user", "parts": [{"functionResponse": response}]})
        else:
            raise ValueError(f"unknown message role: {m['role']!r}")
    return contents


def complete(model, system, messages, tools, base, key):
    """Send one conversation to Gemini and return its neutral reply."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": _to_wire(messages),
        "generationConfig": {"temperature": 0.4,
                             "maxOutputTokens": MODEL_INFO["max_output_tokens"]},
    }
    if tools:
        body["tools"] = [{"functionDeclarations": [t["schema"] for t in tools]}]
    data = post_json(f"{base}/models/{model}:generateContent", body,
                     {"x-goog-api-key": key}, "Gemini")

    candidates = data.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text, calls = [], []
    for part in parts:
        if "functionCall" in part:
            fc = part["functionCall"]
            calls.append({"id": fc.get("id"), "native_id": fc.get("id"), "name": fc["name"],
                          "args": fc.get("args") or {},
                          "signature": part.get("thoughtSignature")})
        elif "text" in part and not part.get("thought"):
            # Thought summaries are the model's scratchpad, not its answer.
            text.append(part["text"])
    meta = data.get("usageMetadata") or {}
    usage = {"input": meta.get("promptTokenCount", 0),
             "output": meta.get("candidatesTokenCount", 0)}
    return {"text": "".join(text), "tool_calls": calls, "usage": usage}
