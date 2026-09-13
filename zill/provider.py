"""Day 1 — The provider: the only file in ZILL that knows about Gemini.

Concept: a model is a function from a conversation to a reply. This module
turns the harness's neutral message format into Gemini's wire format, makes
one HTTP call, and turns the answer back into a plain dict. Nothing else in
the harness imports urllib or knows what a "part" is.

Design rules:
  * Neutral in, neutral out. Callers speak {"role", "text", "tool_calls"};
    only _to_wire and complete() ever see Gemini's JSON shapes.
  * Standard library only: urllib for HTTP, json for bodies.
  * Transient failures (rate limits, 5xx, network drops) retry with
    exponential backoff; permanent failures raise immediately with enough
    of the server's error body to debug from.
"""

import json
import os
import time
import urllib.error
import urllib.request

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.1-pro-preview"
RETRYABLE = {429, 500, 502, 503}  # rate limits and server hiccups: wait them out


def api_key():
    """Return the API key from ZILL_API_KEY, falling back to GEMINI_API_KEY."""
    key = os.environ.get("ZILL_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "No API key found. Set ZILL_API_KEY (or GEMINI_API_KEY) to your "
            "Gemini API key before running the harness."
        )
    return key


def _to_wire(messages):
    """Translate neutral messages into Gemini `contents` entries."""
    contents = []
    for m in messages:
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["text"]}]})
        elif m["role"] == "assistant":
            parts = [{"text": m["text"]}] if m.get("text") else []
            for call in m.get("tool_calls") or []:
                part = {"functionCall": {"name": call["name"], "args": call["args"]}}
                # Gemini 3 rejects a follow-up request unless each functionCall
                # part carries back the exact thoughtSignature it arrived with.
                if call.get("signature"):
                    part["thoughtSignature"] = call["signature"]
                parts.append(part)
            contents.append({"role": "model", "parts": parts})
        elif m["role"] == "tool":
            response = {"name": m["name"], "response": {"result": m["text"]}}
            contents.append({"role": "user", "parts": [{"functionResponse": response}]})
        else:
            raise ValueError(f"unknown message role: {m['role']!r}")
    return contents


def complete(model, system, messages, tools):
    """Send one conversation to the model and return its neutral reply.

    Returns {"text": str, "tool_calls": [{"name", "args", "signature"}],
    "usage": {"input": int, "output": int}}. `tools` is a list of spec dicts,
    each {"schema": <function declaration>}, or empty/None for no tools.
    """
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": _to_wire(messages),
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 65536},
    }
    if tools:
        body["tools"] = [{"functionDeclarations": [t["schema"] for t in tools]}]
    data = _post(f"{API_ROOT}/{model}:generateContent", body)

    candidates = data.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text, calls = [], []
    for part in parts:
        if "functionCall" in part:
            fc = part["functionCall"]
            calls.append({"name": fc["name"], "args": fc.get("args") or {},
                          "signature": part.get("thoughtSignature")})
        elif "text" in part and not part.get("thought"):
            # Thought summaries are the model's scratchpad, not its answer.
            text.append(part["text"])
    meta = data.get("usageMetadata") or {}
    usage = {"input": meta.get("promptTokenCount", 0),
             "output": meta.get("candidatesTokenCount", 0)}
    return {"text": "".join(text), "tool_calls": calls, "usage": usage}


def _post(url, body, retries=5):
    """POST JSON to url and return the decoded reply, retrying transient errors."""
    payload = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key()}
    for attempt in range(retries):
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        last_try = attempt == retries - 1
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")[:400]
            if err.code not in RETRYABLE or last_try:
                raise RuntimeError(f"Gemini API error {err.code}: {detail}") from err
        except (urllib.error.URLError, TimeoutError) as err:
            if last_try:
                raise RuntimeError(f"Gemini API unreachable: {err}") from err
        time.sleep(2 ** attempt * 2)
    raise RuntimeError("Gemini API request failed after retries")
