"""MCP bridge: tools from external MCP servers become ordinary ZILL tools.

Concept: the loop only knows Tool objects. For each trusted server in
.zill/mcp.json, load_tools() starts a client, lists its tools, and wraps
each one as a Tool named mcp__<server>__<tool> whose run() calls the server.
From there Policy, audit, dry-run and sub-agents treat it like any other tool.

Design rules:
  * Untrusted, misconfigured or unreachable servers are skipped with a note;
    one bad server never stops the harness from starting.
  * Risk is "execute", or "network" when the server marks a tool
    openWorldHint. Server annotations never lower a risk, because they are
    untrusted data. Only the user's read_tools list makes a tool "read".
  * Input schemas are reduced to the subset every provider accepts.
  * Results are bounded like bash output; server failures become
    "ERROR: MCP server <name> ..." results the model can read.
"""

import os
import re

from ..tools import Tool
from . import config
from .client import MCPClient, MCPError

KEEP_SCHEMA_KEYS = {"type", "description", "properties", "required", "items", "enum"}
MAX_RESULT_CHARS = 200_000  # a memory bound; the harness shortens and spills past its own cap
NAME_LIMIT = 64  # OpenAI and Anthropic reject longer tool names


def tool_name(server, name):
    """Return the namespaced, provider-safe tool name for server's tool."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return f"mcp__{server}__{safe}"[:NAME_LIMIT]


def clean_schema(schema):
    """Reduce a JSON Schema to the keys every provider understands."""
    if not isinstance(schema, dict):
        return {"type": "string"}
    cleaned = {k: v for k, v in schema.items() if k in KEEP_SCHEMA_KEYS}
    if isinstance(cleaned.get("type"), list):  # ["string", "null"] -> "string"
        cleaned["type"] = next((t for t in cleaned["type"] if t != "null"), "string")
    if "properties" in cleaned:
        cleaned["properties"] = {k: clean_schema(v) for k, v in cleaned["properties"].items()}
    if "items" in cleaned:
        cleaned["items"] = clean_schema(cleaned["items"])
    cleaned.setdefault("type", "object" if "properties" in cleaned else "string")
    return cleaned


def make_tools(client, read_tools=()):
    """Wrap every tool the connected client offers as a ZILL Tool."""
    tools = []
    for spec in client.list_tools():
        original = spec["name"]
        open_world = (spec.get("annotations") or {}).get("openWorldHint")
        risk = "read" if original in read_tools else ("network" if open_world else "execute")
        parameters = clean_schema(spec.get("inputSchema") or {"type": "object"})
        parameters["type"] = "object"
        parameters.setdefault("properties", {})
        name = tool_name(client.name, original)

        def run(_mcp_tool=original, **arguments):
            try:
                is_error, text = client.call_tool(_mcp_tool, arguments)
            except MCPError as err:
                return f"ERROR: {err}"
            if len(text) > MAX_RESULT_CHARS:
                text = text[:MAX_RESULT_CHARS] + f"\n... [clipped at {MAX_RESULT_CHARS} chars]"
            return f"ERROR: {text or 'the tool reported an error'}" if is_error else text

        schema = {"name": name, "parameters": parameters,
                  "description": f"[MCP server {client.name}] {spec.get('description', '')}"}
        tools.append(Tool(name=name, spec={"schema": schema}, run=run, source="mcp", risk=risk))
    return tools


def load_tools(workdir):
    """Start every trusted server in .zill/mcp.json; return (tools, clients, notes)."""
    tools, clients, notes = [], [], []
    try:
        servers = config.load(workdir)
    except RuntimeError as err:
        return [], [], [str(err)]
    for name, server in servers.items():
        if not config.is_trusted(workdir, name, server):
            notes.append(f"MCP server {name} is not approved; check its command, then run "
                         f"`zill mcp trust {name}`")
            continue
        client = MCPClient(name, server["command"], server.get("args", []), server.get("env"),
                           cwd=workdir, timeout=server.get("timeout", 60),
                           log_path=os.path.join(workdir, ".zill", "mcp", f"{name}.log"))
        try:
            client.start()
            tools += make_tools(client, server.get("read_tools", []))
            clients.append(client)
        except MCPError as err:
            client.close()
            notes.append(f"MCP server {name} skipped: {err}")
    return tools, clients, notes
