"""Shared HTTP for adapters: one JSON POST with retries that respect the server.

Design rules:
  * Transient failures (rate limits, overload, 5xx, network drops) retry with
    exponential backoff, waiting at least as long as the server asks, from a
    Retry-After header or a retryDelay in the error body.
  * A 429 for a daily quota is not transient: it raises at once, because
    every retry spends a request that will not come back until the reset.
  * Errors carry the status and the start of the server's body, never the
    request headers, which hold the API key.
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


def post_json(url, body, headers, label, retries=5, timeout=600):
    """POST body as JSON to url and return the decoded reply, retrying transient errors."""
    payload = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", **headers}
    for attempt in range(retries):
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        last_try = attempt == retries - 1
        wait = 2 ** attempt * 2
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            err.close()
            daily = err.code == 429 and DAILY_QUOTA.search(detail)
            if err.code not in RETRYABLE or last_try or daily:
                hint = " (daily quota used up; it resets tomorrow)" if daily else ""
                raise RuntimeError(f"{label} API error {err.code}{hint}: "
                                   f"{detail[:DETAIL_CHARS]}") from err
            wait = max(wait, _server_wait(err.headers or {}, detail))
        except (urllib.error.URLError, TimeoutError) as err:
            if last_try:
                raise RuntimeError(f"{label} API unreachable: {err}") from err
        time.sleep(min(wait, MAX_WAIT))
    raise RuntimeError(f"{label} API request failed after retries")


def _server_wait(headers, detail):
    """Seconds the server asked us to wait, or 0 when it did not say."""
    header = headers.get("retry-after") or headers.get("Retry-After") or ""
    if header.replace(".", "", 1).isdigit():
        return float(header)
    match = RETRY_DELAY.search(detail)
    return float(match.group(1)) if match else 0
