"""Tests for stored credentials, redaction, and the `zill setup` flow."""

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from zill import cli, credentials, provider


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"ZILL_HOME": self._home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._home.cleanup)

    def test_saved_keys_are_owner_only_and_env_wins(self):
        credentials.save("GROQ_API_KEY", "gsk-from-file-123456")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(credentials.path()).st_mode), 0o600)
        self.assertEqual(credentials.get("GROQ_API_KEY"), "gsk-from-file-123456")
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk-from-env-123456"}):
            self.assertEqual(credentials.get("GROQ_API_KEY"), "gsk-from-env-123456")

    def test_redact_masks_file_and_environment_secrets(self):
        credentials.save("DEEPSEEK_API_KEY", "ds-file-secret-123456")
        with mock.patch.dict(os.environ, {"MY_SERVICE_TOKEN": "tok-env-secret-9876"}):
            shown = credentials.redact("key ds-file-secret-123456 and tok-env-secret-9876 ok")
        self.assertEqual(shown, "key [REDACTED] and [REDACTED] ok")

    def test_corrupt_file_is_a_clear_error(self):
        os.makedirs(credentials.home(), exist_ok=True)
        with open(credentials.path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        with self.assertRaisesRegex(RuntimeError, "credentials.json"):
            credentials.get("ANY_KEY")

    def test_setup_saves_pasted_keys_and_default_model(self):
        names = [key_var for _, _, key_var, _ in provider.PROVIDERS.values() if key_var]
        pasted = ["" for _ in names]
        pasted[names.index("ANTHROPIC_API_KEY")] = "sk-ant-pasted-123456"
        clean = {name: "" for name in names + ["ZILL_MODEL", "ZILL_API_KEY"]}
        with mock.patch.dict(os.environ, clean), \
                mock.patch("getpass.getpass", side_effect=pasted), \
                mock.patch("builtins.input", return_value=""), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["setup"]), 0)
        with open(credentials.path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ANTHROPIC_API_KEY": "sk-ant-pasted-123456"})
        self.assertIn("anthropic:claude-opus-5", out.getvalue())
        self.assertNotIn("sk-ant-pasted-123456", out.getvalue())

    def test_headless_run_without_a_key_explains_how_to_fix_it(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ZILL_API_KEY": ""}), \
                redirect_stderr(io.StringIO()) as err:
            code = cli.main(["-p", "hi", "-m", "anthropic:claude-opus-5", "-d", self._home.name])
        self.assertEqual(code, 1)
        self.assertIn("zill setup", err.getvalue())

    def test_terminal_output_is_redacted(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-leak-123456"}), \
                redirect_stdout(io.StringIO()) as out:
            cli.print_event("tool_start", {"name": "bash",
                                           "args": {"command": "echo sk-openai-leak-123456"}})
            cli.print_event("tool_end", {"name": "bash", "result": "sk-openai-leak-123456"})
        self.assertNotIn("sk-openai-leak-123456", out.getvalue())
        self.assertEqual(out.getvalue().count("[REDACTED]"), 2)


if __name__ == "__main__":
    unittest.main()
