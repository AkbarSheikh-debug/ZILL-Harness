"""Tests for stored credentials, redaction, and the `zill setup` flow."""

import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from zill import cli, credentials, provider, settings


def unauthorized():
    body = b'{"error": {"code": 401, "message": "Request had invalid authentication"}}'
    return urllib.error.HTTPError("https://x", 401, "no", {}, io.BytesIO(body))


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
                mock.patch("zill.provider.check_key", return_value=None), \
                mock.patch("builtins.input", return_value=""), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["setup"]), 0)
        with open(credentials.path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ANTHROPIC_API_KEY": "sk-ant-pasted-123456"})
        self.assertIn("anthropic:claude-opus-5", out.getvalue())
        self.assertNotIn("sk-ant-pasted-123456", out.getvalue())

    def _setup(self, pasted, typed, rejected=()):
        """Run setup with pasted keys (in provider order) and typed model answers."""
        names = [key_var for _, _, key_var, _ in provider.PROVIDERS.values() if key_var]
        clean = {name: "" for name in names + ["ZILL_MODEL", "ZILL_API_KEY"]}
        with mock.patch.dict(os.environ, clean), \
                mock.patch("getpass.getpass", side_effect=pasted + [""] * len(names)), \
                mock.patch("zill.provider.check_key",
                           side_effect=lambda name, key: "rejected" if key in rejected else None), \
                mock.patch("builtins.input", side_effect=typed), \
                redirect_stdout(io.StringIO()) as out:
            code = cli.main(["setup"])
        return code, out.getvalue()

    def test_setup_rejects_a_bad_model_name_and_asks_again(self):
        code, out = self._setup(["AIza-good-key-123456"], ["y", "gemini:gemini-3.6-flash"])
        self.assertEqual(code, 0)
        self.assertIn("unknown model 'y'", out)
        self.assertEqual(credentials.get("GEMINI_API_KEY"), "AIza-good-key-123456")
        self.assertIn("Ready", out)

    def test_setup_replaces_an_invalid_saved_model_on_enter(self):
        credentials.save("ZILL_MODEL", "y")
        code, _ = self._setup(["AIza-good-key-123456"], [""])
        self.assertEqual(code, 0)
        self.assertEqual(credentials.get("ZILL_MODEL"), provider.DEFAULT_MODEL)

    def test_setup_does_not_save_a_rejected_key(self):
        code, out = self._setup(["AQ.bad-key-123456"], [""], rejected={"AQ.bad-key-123456"})
        self.assertEqual(code, 1)
        self.assertIn("not saved", out)
        self.assertIn("still needs GEMINI_API_KEY", out)
        self.assertFalse(credentials.get("GEMINI_API_KEY"))

    def test_key_check_reports_a_rejected_key_and_where_to_get_one(self):
        with mock.patch("urllib.request.urlopen", side_effect=[unauthorized()]):
            reason = provider.check_key("gemini", "not-a-valid-key")
        self.assertIn("gemini rejected this key", reason)
        self.assertIn("aistudio.google.com", reason)
        with mock.patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("offline")]):
            with self.assertRaisesRegex(RuntimeError, "unreachable"):
                provider.check_key("gemini", "any-key")

    def test_api_errors_show_the_server_message_and_a_setup_hint(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-x"}), \
                mock.patch("urllib.request.urlopen", side_effect=[unauthorized()]):
            with self.assertRaisesRegex(RuntimeError, r"401 \(the API key was rejected: run "
                                                      r"`zill setup`\): Request had invalid"):
                provider.complete("gemini:gemini-3.6-flash", "sys",
                                  [{"role": "user", "text": "hi"}], [])

    def test_python_without_ssl_is_explained_not_retried(self):
        broken = urllib.error.URLError("unknown url type: https")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-x"}), \
                mock.patch("urllib.request.urlopen", side_effect=[broken]), \
                mock.patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "no ssl module.*installer"):
                provider.complete("gemini:gemini-3.6-flash", "sys",
                                  [{"role": "user", "text": "hi"}], [])
        sleep.assert_not_called()

    def test_model_names_are_checked(self):
        for good in ("gemini:gemini-3.6-flash", "anthropic:claude-opus-5", "ollama:qwen3:8b",
                     "claude-opus-5", "gpt-5", "gemini-3.6-flash"):
            self.assertIsNone(provider.check_model(good), good)
        for bad in ("y", "yes", "openrouter:", "llama3", "foo:bar"):
            self.assertIsNotNone(provider.check_model(bad), bad)
        with self.assertRaisesRegex(RuntimeError, "unknown model 'y'"):
            settings.make_harness(settings.resolve(self._home.name, model="y"), persist=False)

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
