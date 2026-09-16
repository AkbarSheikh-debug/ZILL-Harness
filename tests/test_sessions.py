"""Tests for crash-safe sessions: torn tails, interrupted calls, ids, metadata."""

import glob
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from tests.fake import FakeProvider, call, text
from zill import Harness, tool
from zill import session


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name
        self.path = os.path.join(self.workdir, "s.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_torn_tail_is_dropped(self):
        write_lines(self.path, [json.dumps({"role": "user", "text": "hi"}) + "\n",
                                json.dumps({"role": "assistant", "text": "yo"}) + "\n",
                                '{"role": "user", "te'])
        self.assertEqual([m["text"] for m in session.load(self.path)], ["hi", "yo"])

    def test_interrupted_calls_get_results_by_id(self):
        calls = [{"id": "c1", "name": "bash", "args": {}}, {"id": "c2", "name": "grep", "args": {}}]
        write_lines(self.path, [
            json.dumps({"role": "user", "text": "go"}) + "\n",
            json.dumps({"role": "assistant", "text": "", "tool_calls": calls}) + "\n",
            json.dumps({"role": "tool", "id": "c1", "name": "bash", "text": "ok"}) + "\n"])
        messages = session.load(self.path)
        self.assertEqual(messages[-1], {"role": "tool", "id": "c2", "name": "grep",
                                        "text": session.INTERRUPTED})

    def test_legacy_transcripts_gain_ids(self):
        calls = [{"name": "bash", "args": {}}, {"name": "grep", "args": {}}]
        write_lines(self.path, [
            json.dumps({"role": "assistant", "text": "", "tool_calls": calls}) + "\n",
            json.dumps({"role": "tool", "name": "bash", "text": "ok"}) + "\n"])
        messages = session.load(self.path)
        first, second = messages[0]["tool_calls"]
        self.assertEqual(messages[1]["id"], first["id"])
        self.assertEqual(messages[2]["id"], second["id"])
        self.assertEqual(messages[2]["text"], session.INTERRUPTED)

    def test_ctrl_c_leaves_a_resumable_session(self):
        @tool("Simulates the user pressing Ctrl-C mid-tool.")
        def hang():
            raise KeyboardInterrupt

        with FakeProvider(call("hang")):
            with self.assertRaises(KeyboardInterrupt):
                Harness(self.workdir, extra_tools=[hang], enable_subagents=False).run("hang")
        resumed = Harness(self.workdir, enable_subagents=False)
        self.assertTrue(resumed.resume())
        self.assertEqual(resumed.messages[-1]["text"], session.INTERRUPTED)
        with FakeProvider(text("continuing")):
            self.assertEqual(resumed.run("carry on"), "continuing")

    def test_metadata_is_written_without_secrets(self):
        with mock.patch.dict(os.environ, {"ZILL_API_KEY": "secret-value-123"}):
            with FakeProvider(text("done")):
                harness = Harness(self.workdir, model="test-model", enable_subagents=False)
                harness.run("hello")
        (meta_path,) = glob.glob(os.path.join(self.workdir, session.SESSION_DIR, "*.meta.json"))
        with open(meta_path, encoding="utf-8") as f:
            raw = f.read()
        meta = json.loads(raw)
        self.assertEqual(meta["model"], "test-model")
        self.assertIn("read_file", meta["tools"])
        self.assertEqual(meta["session"], os.path.basename(harness.session_path)[:-len(".jsonl")])
        self.assertNotIn("secret-value-123", raw)
        # harness.workdir is realpath'd (macOS /private, Windows 8.3 names).
        self.assertEqual(session.latest(harness.workdir), harness.session_path)

    def test_concurrent_flushes_do_not_duplicate_messages(self):
        # An app built on the harness may read the transcript (which flushes) from its
        # own thread while the worker thread is flushing the same unrecorded messages.
        harness = Harness(self.workdir, enable_subagents=False)
        harness.session_path = session.new_session(self.workdir, "concurrent")
        harness.messages = [{"role": "user", "text": f"m{i}"} for i in range(20)]
        barrier = threading.Barrier(8)

        def flush():
            barrier.wait()
            harness._flush()

        threads = [threading.Thread(target=flush) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(session.load(harness.session_path)), 20)


if __name__ == "__main__":
    unittest.main()
