"""MCP server: offer ZILL's own tools to another MCP client over stdio.

Concept: `zill mcp serve` turns the harness inside out. Another agent or
editor starts it as a subprocess and calls read_file, grep and friends; each
call runs in the working directory's jail and passes through ZILL's Policy
and audit log exactly as it would for ZILL's own model.

Design rules:
  * Conservative exposure: read-only tools unless --tools names others, and
    the default mode is read-only. There is no person to approve anything,
    so writes need an explicit --mode yolo.
  * A denied or failing call is a tool result with isError true, so the
    calling model can read why. An unknown tool or bad arguments get a
    JSON-RPC error. Nothing crashes the server.
  * stdout carries protocol messages only; diagnostics go to stderr.
"""

import sys
import time

from .. import __version__, audit
from ..security import Policy
from ..tools import core_tools
from . import protocol

DEFAULT_TOOLS = ("read_file", "list_files", "grep")


def handle(message, tools, policy, workdir):
    """Return the reply to one client message, or None for notifications."""
    method, message_id = message.get("method"), message.get("id")
    if message_id is None:
        return None  # notifications (initialized, cancelled) need no answer
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        version = (requested if requested in protocol.SUPPORTED_VERSIONS
                   else protocol.PROTOCOL_VERSION)
        return protocol.result(message_id, {
            "protocolVersion": version, "capabilities": {"tools": {}},
            "serverInfo": {"name": "zill", "version": __version__}})
    if method == "ping":
        return protocol.result(message_id, {})
    if method == "tools/list":
        return protocol.result(message_id, {"tools": [
            {"name": t.name, "description": t.spec["schema"]["description"],
             "inputSchema": t.spec["schema"]["parameters"]} for t in tools.values()]})
    if method == "tools/call":
        params = message.get("params") or {}
        tool = tools.get(params.get("name"))
        arguments = params.get("arguments") or {}
        if tool is None or not isinstance(arguments, dict):
            return protocol.error(message_id, protocol.INVALID_PARAMS,
                                  f"unknown tool {params.get('name')!r} or bad arguments")
        call = {"name": tool.name, "args": arguments}
        decision = policy.decide(call, tool)
        started = time.monotonic()
        if not decision.allowed:
            text = f"BLOCKED: {decision.reason}"
        else:
            try:
                text = str(tool.run(**arguments))
            except Exception as err:  # a failing tool is a result, not a dead server
                text = f"ERROR: {type(err).__name__}: {err}"
        audit.record(workdir, "mcp-serve", call, tool, decision, text,
                     round(time.monotonic() - started, 3))
        failed = text.startswith(("BLOCKED:", "ERROR:"))
        return protocol.result(message_id, {"content": [{"type": "text", "text": text}],
                                            "isError": failed})
    return protocol.error(message_id, protocol.METHOD_NOT_FOUND, f"unsupported method {method}")


def serve(workdir, tool_names=DEFAULT_TOOLS, mode="read-only", stdin=None, stdout=None):
    """Answer MCP messages on stdin until it closes; return an exit code."""
    stdin, stdout = stdin or sys.stdin.buffer, stdout or sys.stdout.buffer
    available = {t.name: t for t in core_tools(workdir)}
    unknown = set(tool_names) - set(available)
    if unknown:
        print(f"zill mcp serve: unknown tools {sorted(unknown)}", file=sys.stderr)
        return 2
    tools = {name: available[name] for name in tool_names}
    policy = Policy(mode)
    print(f"zill mcp serve: {', '.join(tools)} in {workdir} ({mode})", file=sys.stderr)
    for line in stdin:
        if not line.strip():
            continue
        try:
            reply = handle(protocol.decode(line), tools, policy, workdir)
        except protocol.ProtocolError as err:
            reply = protocol.error(None, err.code, str(err))
        if reply is not None:
            stdout.write(protocol.encode(reply))
            stdout.flush()
    return 0
