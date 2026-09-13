"""MCP client: talk to one MCP server running as a local subprocess over stdio.

Concept: an MCP server is a program. The client starts it, performs the
initialize handshake, lists its tools and calls them, matching each reply
to its request by id while a reader thread consumes the server's stdout.

Design rules:
  * The server gets a minimal environment (what programs need to start)
    plus only the variables its config names. ${VAR} values resolve from
    the environment or ZILL's credentials; nothing else leaks through.
  * Its stderr goes to .zill/mcp/<name>.log, never to our stdout, which the
    agent's terminal owns.
  * Every request has a timeout. A server that dies is restarted once on the
    next call; failures raise MCPError, which the tool bridge turns into an
    ERROR result for the model, never a crash.
"""

import atexit
import itertools
import os
import queue
import re
import shutil
import subprocess
import threading
import time

from .. import __version__, credentials
from . import protocol

DEFAULT_TIMEOUT = 60
POLL = 0.1  # seconds between checks that the server is still there
BASE_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "TEMP", "TMP",
            "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL")
VARIABLE = re.compile(r"\$\{(\w+)\}")


class MCPError(RuntimeError):
    """The server could not be reached, timed out, or answered with an error."""


class MCPClient:
    """One connection to one stdio MCP server."""

    def __init__(self, name, command, args=(), env=None, cwd=None,
                 timeout=DEFAULT_TIMEOUT, log_path=None):
        self.name, self.command, self.args = name, command, list(args)
        self.env, self.cwd, self.timeout, self.log_path = env or {}, cwd, timeout, log_path
        self.process, self.server_info, self._ids = None, {}, itertools.count(1)
        self._waiting, self._lock, self._log, self._reader = {}, threading.Lock(), None, None

    def _environment(self):
        """Return the child's environment: the base variables plus the configured ones."""
        env = {k: os.environ[k] for k in BASE_ENV if k in os.environ}

        def lookup(match):
            found = credentials.get(match.group(1))
            if found is None:
                raise MCPError(f"MCP server {self.name} needs ${{{match.group(1)}}}, "
                               f"which is not set")
            return found

        for key, value in self.env.items():
            env[key] = VARIABLE.sub(lookup, str(value))
        return env

    def alive(self):
        """True while the server process is running."""
        return self.process is not None and self.process.poll() is None

    def start(self):
        """Launch the server and complete the initialize handshake."""
        self.close()
        env = self._environment()
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            self._log = open(self.log_path, "ab")
        try:
            self.process = subprocess.Popen(
                [shutil.which(self.command) or self.command, *self.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._log or subprocess.DEVNULL, cwd=self.cwd, env=env)
        except OSError as err:
            raise MCPError(f"cannot start MCP server {self.name} ({self.command}): {err}") from err
        atexit.register(self.close)
        self._reader = threading.Thread(target=self._read, args=(self.process,), daemon=True)
        self._reader.start()
        reply = self._request("initialize", {
            "protocolVersion": protocol.PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "zill", "version": __version__}})
        if reply.get("protocolVersion") not in protocol.SUPPORTED_VERSIONS:
            self.close()
            raise MCPError(f"MCP server {self.name} speaks protocol "
                           f"{reply.get('protocolVersion')!r}; ZILL supports "
                           f"{', '.join(protocol.SUPPORTED_VERSIONS)}")
        self.server_info = reply.get("serverInfo") or {}
        self._send(protocol.notification("notifications/initialized"))

    def list_tools(self):
        """Return every tool the server offers, following pagination cursors."""
        tools, cursor = [], None
        while True:
            reply = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools += reply.get("tools") or []
            cursor = reply.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, arguments):
        """Call a tool; return (is_error, text). Restarts a dead server once."""
        if not self.alive():
            self.start()
        reply = self._request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for block in reply.get("content") or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(f"[{block.get('type', 'unknown')} content omitted]")
        if not parts and reply.get("structuredContent") is not None:
            parts.append(str(reply["structuredContent"]))
        return bool(reply.get("isError")), "\n".join(parts)

    def close(self):
        """Stop the server process, if running."""
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                process.stdin.close()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        if self._log:
            self._log.close()
            self._log = None

    def _send(self, message):
        """Write one message to the server."""
        process = self.process
        if process is None or process.poll() is not None:
            raise MCPError(f"MCP server {self.name} is not running")
        try:
            with self._lock:
                process.stdin.write(protocol.encode(message))
                process.stdin.flush()
        except OSError as err:
            raise MCPError(f"MCP server {self.name} disconnected: {err}") from err

    def _request(self, method, params):
        """Send a request and wait for its reply; return the result or raise MCPError."""
        message_id = next(self._ids)
        waiter = self._waiting[message_id] = queue.Queue(maxsize=1)
        deadline = time.monotonic() + self.timeout
        try:
            self._send(protocol.request(message_id, method, params))
            while True:
                try:
                    reply = waiter.get(timeout=POLL)
                    break
                except queue.Empty:
                    # The reader thread ends only after routing every reply it saw.
                    if not self._reader.is_alive() and waiter.empty():
                        reply = None
                        break
                    if time.monotonic() > deadline:
                        raise MCPError(f"MCP server {self.name} did not answer {method} "
                                       f"within {self.timeout}s") from None
        finally:
            self._waiting.pop(message_id, None)
        if reply is None:
            raise MCPError(f"MCP server {self.name} disconnected during {method} "
                           f"(see {self.log_path or 'its stderr'})")
        if "error" in reply:
            error = reply["error"] or {}
            raise MCPError(f"MCP server {self.name} refused {method}: "
                           f"{error.get('message')} (code {error.get('code')})")
        return reply.get("result") or {}

    def _read(self, process):
        """Reader thread: route replies to waiters, answer pings, drop the rest."""
        for line in process.stdout:
            try:
                message = protocol.decode(line)
            except protocol.ProtocolError:
                continue  # a stray non-protocol line on stdout: ignore it
            if protocol.is_response(message):
                waiter = self._waiting.get(message.get("id"))
                if waiter is not None:
                    waiter.put(message)
            elif "id" in message:  # a request from the server
                reply = (protocol.result(message["id"], {}) if message["method"] == "ping" else
                         protocol.error(message["id"], protocol.METHOD_NOT_FOUND,
                                        f"ZILL does not support {message['method']}"))
                try:
                    self._send(reply)
                except MCPError:
                    break
