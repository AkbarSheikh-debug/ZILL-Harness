"""ZILL Harness: a concise coding-agent harness built one layer at a time.

Harness composes the layers; Policy gates tool calls; Tool and tool let
callers hand the agent their own functions; run_fleet runs many harnesses
side by side.
"""

__version__ = "1.0.0"  # defined before the imports below: harness.py reads it
UI_API = 2  # the Harness surface zill-ui builds on; bumped when the app needs more of it

from .fleet import run_fleet  # noqa: E402
from .harness import Harness  # noqa: E402
from .security import Policy  # noqa: E402
from .tools import Tool, tool  # noqa: E402

__all__ = ["Harness", "Policy", "Tool", "__version__", "run_fleet", "tool"]
