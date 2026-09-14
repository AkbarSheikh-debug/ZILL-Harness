"""Release checks: the promises ZILL makes, verified mechanically on every PR.

  * The core imports only the Python standard library.
  * No real API keys or tokens are committed.
  * The version, the changelog and the package metadata agree.
  * The final-check commands (--help, doctor, inspect) work and --json parses.
"""

import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import zill
from zill import cli

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Real key formats only; the fake keys in tests are deliberately shaped differently.
SECRET_PATTERNS = {
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "Google auth token": r"AQ\.[A-Za-z0-9_-]{40,}",
    "Anthropic key": r"sk-ant-(?:api|admin)\d\d-[A-Za-z0-9_-]{20,}",
    "OpenAI project key": r"sk-proj-[A-Za-z0-9_-]{20,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{36}",
    "GitHub fine-grained token": r"github_pat_[A-Za-z0-9_]{50,}",
}


class ReleaseChecks(unittest.TestCase):
    def test_core_imports_only_the_standard_library(self):
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:
            self.skipTest("sys.stdlib_module_names needs Python 3.10+")
        outside = []
        for dirpath, _, filenames in os.walk(os.path.join(REPO, "zill")):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        modules = [node.module or ""]
                    else:
                        continue
                    for module in modules:
                        top = module.split(".")[0]
                        if top not in stdlib and top != "zill":
                            outside.append(f"{os.path.relpath(path, REPO)}: {module}")
        self.assertEqual(outside, [], "the core must stay standard-library only")

    def test_no_real_secrets_are_committed(self):
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        listed = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True)
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        found = []
        for rel in listed.stdout.splitlines():
            path = os.path.join(REPO, rel)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for label, pattern in SECRET_PATTERNS.items():
                if re.search(pattern, content):
                    found.append(f"{rel}: {label}")
        self.assertEqual(found, [])

    def test_version_changelog_and_metadata_agree(self):
        with open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8") as f:
            changelog = f.read()
        self.assertIn(f"## [{zill.__version__}]", changelog)
        with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as f:
            pyproject = f.read()
        self.assertIn('version = { attr = "zill.__version__" }', pyproject)
        self.assertIn("dependencies = []", pyproject)

    def test_zill_ui_explains_how_to_add_the_app_when_it_is_missing(self):
        with mock.patch("importlib.import_module", side_effect=ImportError), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["ui"]), 1)
        self.assertIn('pip install "zill-harness[ui]"', err.getvalue())

    def test_final_check_commands(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"ZILL_HOME": tmp, "GEMINI_API_KEY": "gm-release-check-1"}), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as help_exit:
                cli.main(["--help"])
            self.assertEqual(help_exit.exception.code, 0)
            out.truncate(0)
            out.seek(0)
            self.assertEqual(cli.main(["inspect", "-d", tmp, "--json"]), 0)
            inspect = json.loads(out.getvalue())
            out.truncate(0)
            out.seek(0)
            self.assertEqual(cli.main(["doctor", "-d", tmp, "--json"]), 0)
            doctor = json.loads(out.getvalue())
        self.assertIn("read_file", [t["name"] for t in inspect["tools"]])
        self.assertTrue(doctor["ok"])


if __name__ == "__main__":
    unittest.main()
