"""Cost: what a session's tokens cost, when the price is actually known.

Concept: every model reply reports input and output tokens; Harness adds
them up. This module turns totals into dollars using a small price table.

Design rules:
  * Prices are USD per million tokens, copied from the provider's published
    list prices, and only for models documented here. An unknown model
    reports tokens and no dollar figure, never a guessed one.
  * "prices" in ~/.zill/config.json adds or overrides entries:
    {"gemini:gemini-3.6-flash": [0.30, 2.50]}.
  * All input is charged at the full input rate, so with prompt caching the
    figure is an upper bound. Summaries made by compaction are not counted.
"""

from . import provider

# Anthropic first-party list prices (input, output), USD per 1M tokens, as of 2026-06.
PRICES = {
    "anthropic:claude-fable-5-1": (10.0, 50.0),
    "anthropic:claude-fable-5": (10.0, 50.0),
    "anthropic:claude-opus-5": (5.0, 25.0),
    "anthropic:claude-opus-4-8": (5.0, 25.0),
    "anthropic:claude-opus-4-7": (5.0, 25.0),
    "anthropic:claude-opus-4-6": (5.0, 25.0),
    "anthropic:claude-sonnet-5": (2.0, 10.0),
    "anthropic:claude-sonnet-4-6": (3.0, 15.0),
    "anthropic:claude-haiku-4-5": (1.0, 5.0),
}


def model_key(model):
    """Return the canonical "provider:model" key for model."""
    name, _, _, _, model_id = provider.resolve(model)
    return f"{name}:{model_id}"


def estimate(model, usage, overrides=None):
    """Return the USD cost of usage on model, or None when the price is unknown."""
    key = model_key(model)
    price = (overrides or {}).get(key) or PRICES.get(key)
    if price is None:
        return None
    return (usage.get("input", 0) * price[0] + usage.get("output", 0) * price[1]) / 1_000_000


def describe(model, usage, overrides=None):
    """Return a one-line summary of calls, tokens and (when known) dollars."""
    line = (f"{usage.get('calls', 0)} model calls, {usage.get('input', 0):,} input and "
            f"{usage.get('output', 0):,} output tokens")
    dollars = estimate(model, usage, overrides)
    if dollars is None:
        return (f"{line}; price unknown for {model_key(model)} "
                f"(add it under \"prices\" in ~/.zill/config.json)")
    return f"{line}; about ${dollars:.4f} (upper bound)"
