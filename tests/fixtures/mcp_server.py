"""A tiny stdio MCP server for tests, written without any ZILL code.

It speaks newline-delimited JSON-RPC 2.0 straight from the MCP spec, so the
client is tested against the protocol, not against its own helpers.
Tools: add, echo, failing_tool, crash, slow, show_env. tools/list is split
across two pages, and the first tools/list first sends the client a ping.
"""

import json
import os
import sys
import time

TOOLS = [
    {"name": "add", "description": "Add two numbers.",
     "inputSchema": {"type": "object", "$schema": "http://json-schema.org/draft-07/schema#",
                     "properties": {"a": {"type": "number"}, "b": {"type": ["number", "null"]}},
                     "required": ["a", "b"], "additionalProperties": False}},
    {"name": "echo", "description": "Echo text.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "failing_tool", "description": "Always fails.", "inputSchema": {"type": "object"}},
    {"name": "crash", "description": "Kills the server.", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "Sleeps.", "inputSchema": {"type": "object"},
     "annotations": {"openWorldHint": True, "readOnlyHint": True}},
    {"name": "show_env", "description": "Shows two variables.", "inputSchema": {"type": "object"}},
]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def reply(message_id, result):
    send({"jsonrpc": "2.0", "id": message_id, "result": result})


def text(message_id, value, is_error=False):
    reply(message_id, {"content": [{"type": "text", "text": value}], "isError": is_error})


pinged = False
for line in sys.stdin:
    message = json.loads(line)
    method, message_id = message.get("method"), message.get("id")
    params = message.get("params") or {}
    if message_id is None:
        continue
    if method == "initialize":
        reply(message_id, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fixture", "version": "1"}})
    elif method == "tools/list":
        if not pinged:  # a server-to-client request the client must answer
            pinged = True
            send({"jsonrpc": "2.0", "id": "srv-ping", "method": "ping"})
            answer = json.loads(sys.stdin.readline())
            assert answer.get("id") == "srv-ping" and "result" in answer, answer
        if params.get("cursor") == "page2":
            reply(message_id, {"tools": TOOLS[3:]})
        else:
            reply(message_id, {"tools": TOOLS[:3], "nextCursor": "page2"})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "add":
            text(message_id, str(args["a"] + args["b"]))
        elif name == "echo":
            text(message_id, args.get("text", ""))
        elif name == "failing_tool":
            text(message_id, "this tool always fails", is_error=True)
        elif name == "crash":
            os._exit(3)
        elif name == "slow":
            time.sleep(float(args.get("seconds", 5)))
            text(message_id, "done sleeping")
        elif name == "show_env":
            text(message_id, f"{os.environ.get('FIXTURE_TOKEN', 'unset')} "
                             f"{os.environ.get('ZILL_TEST_LEAK', 'no-leak')}")
        else:
            send({"jsonrpc": "2.0", "id": message_id,
                  "error": {"code": -32602, "message": f"unknown tool {name}"}})
    else:
        send({"jsonrpc": "2.0", "id": message_id,
              "error": {"code": -32601, "message": f"no method {method}"}})
