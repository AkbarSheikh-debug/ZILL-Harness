"""Shared HTTP for adapters: JSON POSTs and SSE streams with retries that respect the server.

Design rules:
  * Transient failures (rate limits, overload, 5xx, network drops) retry with
    exponential backoff, waiting at least as long as the server asks, from a
    Retry-After header or a retryDelay in the error body.
  * A 429 for a daily quota is not transient: it raises at once, because
    every retry spends a request that will not come back until the reset.
  * Retries happen only while opening the request. Once a stream has begun,
    a failure raises, because replaying half a reply would duplicate it.
  * Errors carry the status and the server's own message (or the start of
    its body), never the request headers, which hold the API key.
"""

import json
import re
import time
import urllib.error
import urllib.request

RETRYABLE = {429, 500, 502, 503, 504, 529}  # 529: Anthropic's "overloaded"
MAX_WAIT = 60
DETAIL_CHARS = 400
DAILY_QUOTA = re.compile(r"PerDay", re.IGNORECASE)
RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


class APIError(RuntimeError):
    """An error reply from a model API; code is its HTTP status."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def get_json(url, headers, label, timeout=15):
    """GET url once and return the decoded reply; used for cheap checks such as a key's validity."""
    with _open(url, None, headers, label, 1, timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url, body, headers, label, retries=5, timeout=600):
    """POST body as JSON to url and return the decoded reply, retrying transient errors."""
    with _open(url, body, headers, label, retries, timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_sse(url, body, headers, label, retries=5, timeout=600):
    """POST body and yield each server-sent event's JSON data until the stream ends."""
    with _open(url, body, headers, label, retries, timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue  # blank separators, "event:" names, ":" keep-alives
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError as err:
                raise RuntimeError(f"{label} sent a malformed stream event: {data[:200]}") from err


def _open(url, body, headers, label, retries, timeout):
    """Open the request (a GET when body is None), retrying transient failures; return the response."""
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", **headers}
    for attempt in range(retries):
        request = urllib.request.Request(url, data=payload, headers=headers,
                                         method="GET" if payload is None else "POST")
        last_try = attempt == retries - 1
        wait = 2 ** attempt * 2
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            err.close()
            daily = err.code == 429 and DAILY_QUOTA.search(detail)
            if err.code not in RETRYABLE or last_try or daily:
                hint = (" (daily quota used up; it resets tomorrow)" if daily else
                        " (the API key was rejected: run `zill setup`)"
                        if err.code in (401, 403) else "")
                raise APIError(f"{label} API error {err.code}{hint}: {_message(detail)}",
                               err.code) from err
            wait = max(wait, _server_wait(err.headers or {}, detail))
        except (urllib.error.URLError, TimeoutError) as err:
            if last_try:
                raise RuntimeError(f"{label} API unreachable: {err}") from err
        time.sleep(min(wait, MAX_WAIT))
    raise RuntimeError(f"{label} API request failed after retries")


def _message(detail):
    """The server's own error message from a JSON error body, else the body's start."""
    try:
        error = json.loads(detail).get("error")
        message = error.get("message") if isinstance(error, dict) else error
    except (ValueError, AttributeError):
        message = None
    return message if isinstance(message, str) and message else detail[:DETAIL_CHARS]


def _server_wait(headers, detail):
    """Seconds the server asked us to wait, or 0 when it did not say."""
    header = headers.get("retry-after") or headers.get("Retry-After") or ""
    if header.replace(".", "", 1).isdigit():
        return float(header)
    match = RETRY_DELAY.search(detail)
    return float(match.group(1)) if match else 0
