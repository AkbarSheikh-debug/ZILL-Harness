"""MCP protocol: the JSON-RPC 2.0 messages ZILL speaks, built and checked in one place.

Concept: the Model Context Protocol is JSON-RPC 2.0 over a transport. This
module is the only code that builds or validates those dictionaries; the
client and server work with its helpers, never with raw JSON-RPC shapes.

Supported subset, pinned to spec revision 2025-06-18 (older 2025-03-26 and
2024-11-05 servers are accepted, because these methods are unchanged there):
  initialize, notifications/initialized, ping, tools/list (with cursor
  pagination), tools/call. Not implemented: resources, prompts, sampling,
  roots, elicitation, logging, progress, cancellation, and the HTTP transport.

Design rules:
  * Messages are single-line JSON separated by newlines (the stdio transport).
  * Anything malformed raises ProtocolError with a useful message; nothing
    malformed reaches the rest of ZILL.
"""

import json

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ProtocolError(Exception):
    """A message that is not valid JSON-RPC 2.0."""

    def __init__(self, message, code=INVALID_REQUEST):
        super().__init__(message)
        self.code = code


def request(message_id, method, params=None):
    """Return a JSON-RPC request."""
    message = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method, params=None):
    """Return a JSON-RPC notification (a request without an id)."""
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def result(message_id, value):
    """Return a successful JSON-RPC response."""
    return {"jsonrpc": "2.0", "id": message_id, "result": value}


def error(message_id, code, text):
    """Return a JSON-RPC error response."""
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def encode(message):
    """Serialise message as one newline-terminated line of UTF-8 JSON."""
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line):
    """Parse and validate one line; return the message dict or raise ProtocolError."""
    try:
        message = json.loads(line)
    except (ValueError, UnicodeDecodeError) as err:
        raise ProtocolError(f"not JSON: {str(line)[:120]!r}", PARSE_ERROR) from err
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise ProtocolError("not a JSON-RPC 2.0 message")
    if not ("method" in message or "result" in message or "error" in message):
        raise ProtocolError("message has no method, result or error")
    if "method" in message and not isinstance(message["method"], str):
        raise ProtocolError("method must be a string")
    return message


def is_response(message):
    """True for a result or error reply to one of our requests."""
    return "method" not in message and "id" in message
