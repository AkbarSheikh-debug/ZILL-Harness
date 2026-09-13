"""Tests for v0.7: MCP client and server, trust, plugins, connectors, web tools."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests.fake import FakeProvider, call, text
from zill import Harness, Policy, cli, web
from zill.mcp import clean_schema, config as mcp_config, make_tools, protocol, server
from zill.mcp.client import MCPClient, MCPError

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "mcp_server.py")
EXAMPLES = os.path.join(REPO, "examples")


class Home(unittest.TestCase):
    """A temp project and ZILL_HOME for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = os.path.realpath(os.path.join(self._tmp.name, "project"))
        os.makedirs(self.workdir)
        patcher = mock.patch.dict(os.environ, {"ZILL_HOME": os.path.join(self._tmp.name, "home")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def cli(self, *argv):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def harness(self, **kwargs):
        kwargs.setdefault("persist", False)
        harness = Harness(self.workdir, **kwargs)
        self.addCleanup(harness.close)
        return harness

    def results(self, harness):
        return [m["text"] for m in harness.messages if m["role"] == "tool"]

    def fixture_client(self, **kwargs):
        client = MCPClient("fx", sys.executable, [FIXTURE], cwd=self.workdir, **kwargs)
        self.addCleanup(client.close)
        client.start()
        return client


class MCPClientTests(Home):
    def test_handshake_pagination_ping_and_calls(self):
        client = self.fixture_client()
        self.assertEqual(client.server_info["name"], "fixture")
        names = [t["name"] for t in client.list_tools()]  # two pages, plus a server ping
        self.assertEqual(names, ["add", "echo", "failing_tool", "crash", "slow", "show_env"])
        self.assertEqual(client.call_tool("add", {"a": 2, "b": 3}), (False, "5"))
        self.assertEqual(client.call_tool("failing_tool", {}), (True, "this tool always fails"))
        with self.assertRaisesRegex(MCPError, "refused tools/call"):
            client.call_tool("no_such_tool", {})

    def test_bridge_names_schemas_risks_and_errors(self):
        client = self.fixture_client()
        tools = {t.name: t for t in make_tools(client, read_tools=["echo"])}
        add = tools["mcp__fx__add"]
        self.assertEqual(add.source, "mcp")
        self.assertEqual(add.spec["schema"]["parameters"],
                         {"type": "object", "properties": {"a": {"type": "number"},
                                                           "b": {"type": "number"}},
                          "required": ["a", "b"]})
        self.assertEqual({n: t.risk for n, t in tools.items() if n.endswith(("echo", "slow", "add"))},
                         {"mcp__fx__add": "execute", "mcp__fx__echo": "read",
                          "mcp__fx__slow": "network"})  # readOnlyHint never lowers risk
        self.assertEqual(tools["mcp__fx__failing_tool"].run(), "ERROR: this tool always fails")

    def test_a_crashed_server_is_an_error_then_restarts(self):
        client = self.fixture_client()
        tools = {t.name: t for t in make_tools(client)}
        self.assertIn("disconnected", tools["mcp__fx__crash"].run())
        self.assertEqual(tools["mcp__fx__add"].run(a=1, b=1), "2")

    def test_requests_time_out(self):
        client = self.fixture_client(timeout=1)
        tools = {t.name: t for t in make_tools(client)}
        self.assertIn("did not answer tools/call within 1s", tools["mcp__fx__slow"].run(seconds="5"))

    def test_the_server_sees_only_the_environment_it_was_given(self):
        with mock.patch.dict(os.environ, {"MY_TOKEN": "tok-123", "ZILL_TEST_LEAK": "leaked"}):
            client = self.fixture_client(env={"FIXTURE_TOKEN": "${MY_TOKEN}"})
            self.assertEqual(client.call_tool("show_env", {}), (False, "tok-123 no-leak"))
        with self.assertRaisesRegex(MCPError, "needs \\$\\{NOT_SET_ANYWHERE\\}"):
            MCPClient("fx", sys.executable, [FIXTURE], env={"X": "${NOT_SET_ANYWHERE}"}).start()

    def test_protocol_rejects_malformed_messages(self):
        for bad in ("not json", "[]", '{"jsonrpc": "1.0", "method": "x"}', '{"jsonrpc": "2.0"}'):
            with self.assertRaises(protocol.ProtocolError):
                protocol.decode(bad)
        self.assertEqual(clean_schema({"type": ["null", "string"], "format": "uri"}),
                         {"type": "string"})


class MCPHarnessTests(Home):
    def add_fixture(self):
        code, out, err = self.cli("mcp", "add", "-d", self.workdir, "fx", "--",
                                  sys.executable, FIXTURE)
        self.assertEqual(code, 0, err)

    def test_servers_in_a_project_do_not_start_until_approved(self):
        mcp_config.save(self.workdir, {"fx": {"command": sys.executable, "args": [FIXTURE]}})
        harness = self.harness()
        self.assertFalse([n for n in harness.tools if n.startswith("mcp__")])
        self.assertIn("not approved", harness.notes[0])

    def test_approved_server_tools_run_through_policy_and_audit(self):
        self.add_fixture()
        with FakeProvider(call("mcp__fx__add", a=2, b=40), text("done")):
            harness = self.harness(persist=True)
            harness.run("add")
        self.assertEqual(self.results(harness), ["42"])
        with open(os.path.join(self.workdir, ".zill", "audit.jsonl"), encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual((entry["tool"], entry["source"], entry["risk"]),
                         ("mcp__fx__add", "mcp", "execute"))
        with FakeProvider(call("mcp__fx__add", a=1, b=1), text("done")):
            dry = self.harness(policy=Policy("yolo", dry_run=True))
            dry.run("add")
        self.assertTrue(self.results(dry)[0].startswith("BLOCKED: dry-run"))

    def test_editing_an_approved_server_needs_approval_again(self):
        self.add_fixture()
        servers = mcp_config.load(self.workdir)
        servers["fx"]["args"].append("--changed")
        mcp_config.save(self.workdir, servers)
        self.assertIn("not approved", self.harness().notes[0])

    def test_sub_agents_share_the_parents_mcp_tools(self):
        self.add_fixture()
        script = [call("spawn_agent", task="add 2 and 2"), call("mcp__fx__add", a=2, b=2),
                  text("child says 4"), text("parent done")]
        with FakeProvider(*script):
            harness = self.harness()
            harness.run("delegate")
        self.assertEqual(self.results(harness), ["child says 4"])
        self.assertEqual(len(harness._mcp_clients), 1)


class MCPServeTests(Home):
    def serve_client(self, *extra):
        with open(os.path.join(self.workdir, "hello.txt"), "w", encoding="utf-8") as f:
            f.write("hi from the jail")
        client = MCPClient("zill", sys.executable,
                           ["-m", "zill", "mcp", "serve", "-d", self.workdir,
                            "--tools", "read_file,write_file", *extra], cwd=REPO)
        self.addCleanup(client.close)
        client.start()
        return client

    def test_zill_serves_its_tools_through_policy(self):
        client = self.serve_client()
        self.assertEqual(sorted(t["name"] for t in client.list_tools()), ["read_file", "write_file"])
        self.assertEqual(client.call_tool("read_file", {"path": "hello.txt"}),
                         (False, "1\thi from the jail"))
        is_error, reply = client.call_tool("write_file", {"path": "x.txt", "content": "x"})
        self.assertTrue(is_error)
        self.assertIn("BLOCKED", reply)
        self.assertFalse(os.path.exists(os.path.join(self.workdir, "x.txt")))
        self.assertTrue(client.call_tool("read_file", {"path": "../outside"})[0])
        with self.assertRaisesRegex(MCPError, "unknown tool"):
            client.call_tool("bash", {"command": "echo hi"})

    def test_writes_need_an_explicit_mode(self):
        client = self.serve_client("--mode", "yolo")
        self.assertEqual(client.call_tool("write_file", {"path": "x.txt", "content": "x"})[0], False)
        self.assertTrue(os.path.exists(os.path.join(self.workdir, "x.txt")))

    def test_version_negotiation(self):
        tools = {}
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2024-11-05"}}, tools, Policy(), ".")
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        reply = server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                               "params": {"protocolVersion": "1999-01-01"}}, tools, Policy(), ".")
        self.assertEqual(reply["result"]["protocolVersion"], protocol.PROTOCOL_VERSION)


class PluginTests(Home):
    def install(self, example, name):
        target = os.path.join(self.workdir, ".zill", "plugins", name)
        shutil.copytree(os.path.join(EXAMPLES, example), target)
        return target

    def write_plugin(self, name, manifest, code):
        folder = os.path.join(self.workdir, ".zill", "plugins", name)
        os.makedirs(folder)
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"name": name, "version": "1", "entrypoint": "plugin:register", **manifest}, f)
        with open(os.path.join(folder, "plugin.py"), "w", encoding="utf-8") as f:
            f.write(code)
        return folder

    def test_plugins_load_only_after_enable_and_edits_disable_them(self):
        folder = self.install("plugins/hello_plugin", "hello")
        self.assertNotIn("plugin__hello__hello", self.harness().tools)
        code, out, _ = self.cli("plugin", "enable", "hello", "-d", self.workdir, "--yes")
        self.assertEqual(code, 0)
        self.assertIn("permissions: read", out)
        with FakeProvider(call("plugin__hello__hello", name="Ada"), text("done")):
            harness = self.harness()
            harness.run("greet")
        self.assertEqual(harness.tools["plugin__hello__hello"].source, "plugin")
        self.assertEqual(self.results(harness), ["Hello, Ada! (from the hello plugin)"])
        with open(os.path.join(folder, "plugin.py"), "a", encoding="utf-8") as f:
            f.write("\n# edited\n")
        changed = self.harness()
        self.assertNotIn("plugin__hello__hello", changed.tools)
        self.assertIn("changed since it was enabled", changed.notes[0])
        _, listing, _ = self.cli("plugin", "list", "-d", self.workdir)
        self.assertIn("changed", listing)

    def test_undeclared_permissions_and_broken_plugins_are_notes(self):
        greedy = "from zill import tool\ndef register(r):\n" \
                 "    @tool('x', risk='write')\n    def sneaky():\n        return 1\n" \
                 "    r.add_tool(sneaky)\n"
        self.write_plugin("greedy", {"permissions": ["read"]}, greedy)
        self.write_plugin("broken", {"permissions": ["read"]}, "def register(r):\n    1/0\n")
        for name in ("greedy", "broken"):
            self.cli("plugin", "enable", name, "-d", self.workdir, "--yes")
        harness = self.harness()
        notes = " | ".join(harness.notes)
        self.assertIn("needs the \"write\" permission", notes)
        self.assertIn("broken failed to load: ZeroDivisionError", notes)

    def test_local_notes_connector_and_profiles(self):
        self.install("connectors/local_notes", "local_notes")
        self.cli("plugin", "enable", "local_notes", "-d", self.workdir, "--yes")
        script = [call("connector__local_notes__create_note", name="ideas", text="- ship v0.7"),
                  call("connector__local_notes__list_notes"),
                  call("connector__local_notes__read_note", name="../../secrets"), text("done")]
        with FakeProvider(*script):
            harness = self.harness()
            harness.run("notes")
        created, listed, escaped = self.results(harness)
        self.assertEqual((created, listed), ("saved note ideas", "ideas"))
        self.assertIn("ERROR: ValueError", escaped)
        self.assertEqual(harness.tools["connector__local_notes__list_notes"].source, "connector")
        with FakeProvider(call("connector__local_notes__create_note", name="x", text="y"),
                          text("done")):
            reviewer = self.harness(policy=Policy("read-only"))
            reviewer.run("try to write")
        self.assertIn("read-only", self.results(reviewer)[0])

    def test_connectors_without_credentials_explain_themselves(self):
        code = "from zill import tool\ndef register(r):\n" \
               "    @tool('Search issues.', risk='network', query='q')\n" \
               "    def search(query):\n        return r.credential('ISSUES_TOKEN')\n" \
               "    r.add_tool(search)\n"
        self.write_plugin("issues", {"kind": "connector", "permissions": ["network"],
                                     "credentials": ["ISSUES_TOKEN"]}, code)
        self.cli("plugin", "enable", "issues", "-d", self.workdir, "--yes")
        with FakeProvider(call("connector__issues__search", query="bug"), text("done")):
            harness = self.harness()
            harness.run("search")
        self.assertIn("issues connector is not configured: set ISSUES_TOKEN",
                      self.results(harness)[0])


class WebTests(unittest.TestCase):
    PAGE = (b"<html><head><title>Docs</title><script>alert(1)</script></head><body>"
            b"<h1>Install</h1><p>Run   pip install zill.</p><style>p{}</style></body></html>")

    def fetch(self, body, content_type="text/html; charset=utf-8", url="https://example.com"):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = body
        response.__enter__.return_value.headers = {"Content-Type": content_type}
        with mock.patch.dict(os.environ, {"BRAVE_API_KEY": "", "TAVILY_API_KEY": ""}), \
                mock.patch("urllib.request.urlopen", return_value=response):
            tools = {t.name: t for t in web.web_tools()}
            return tools, tools["web_fetch"].run(url=url)

    def test_fetch_turns_html_into_text(self):
        tools, page = self.fetch(self.PAGE)
        self.assertEqual(page, "# Docs\n\nInstall\nRun pip install zill.")
        self.assertEqual(tools["web_fetch"].risk, "network")
        self.assertNotIn("web_search", tools)

    def test_fetch_refuses_other_schemes_and_binaries(self):
        self.assertIn("only http and https", self.fetch(b"", url="file:///etc/passwd")[1])
        self.assertIn("not a text page", self.fetch(b"\x89PNG", content_type="image/png")[1])
        self.assertIn("clipped at", self.fetch(b"x" * 30000, content_type="text/plain")[1])

    def test_search_uses_the_key_in_a_header_not_the_url(self):
        captured = []

        def urlopen(request, timeout=None):
            captured.append(request)
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({"web": {"results": [
                {"title": "ZILL", "url": "https://z.dev", "description": "a harness"}]}}).encode()
            response.__enter__.return_value.headers = {"Content-Type": "application/json"}
            return response

        with mock.patch.dict(os.environ, {"BRAVE_API_KEY": "brave-secret-123"}), \
                mock.patch("urllib.request.urlopen", urlopen):
            search = {t.name: t for t in web.web_tools()}["web_search"]
            self.assertEqual(search.run(query="zill harness"), "ZILL\nhttps://z.dev\na harness")
        self.assertNotIn("brave-secret-123", captured[0].full_url)
        self.assertEqual(captured[0].get_header("X-subscription-token"), "brave-secret-123")


if __name__ == "__main__":
    unittest.main()
