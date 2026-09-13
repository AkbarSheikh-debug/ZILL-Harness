"""Test doubles: a scripted provider, so no test needs a network or an API key.

FakeProvider replaces zill.provider.complete for the duration of a `with`
block. Every module calls provider.complete at call time, so the loop, the
context engine and sub-agents all talk to the script instead of Gemini.
"""

import copy
from unittest import mock

from zill import provider

USAGE = {"input": 0, "output": 0}


def text(reply):
    """A scripted reply with final text and no tool calls."""
    return {"text": reply, "tool_calls": [], "usage": USAGE}


def call(tool_name, /, **args):
    """A scripted reply asking for one tool call (args may include "name")."""
    return {"text": "", "tool_calls": [{"name": tool_name, "args": args}], "usage": USAGE}


class FakeProvider:
    """Replay scripted replies in order and record every request made."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, model, system, messages, tools):
        """Record the request and return the next scripted reply."""
        self.requests.append({"system": system, "messages": copy.deepcopy(messages),
                              "tools": [t["schema"]["name"] for t in tools or []]})
        if not self.replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        return copy.deepcopy(self.replies.pop(0))  # the loop adds ids in place

    def __enter__(self):
        self._patch = mock.patch.object(provider, "complete", self.complete)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
