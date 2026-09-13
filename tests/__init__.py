"""Test package. Point ZILL_HOME at an empty temp directory before any test runs,
so no test ever reads or writes the developer's real ~/.zill credentials."""

import os
import tempfile

os.environ["ZILL_HOME"] = tempfile.mkdtemp(prefix="zill-test-home-")
