"""The provider: one neutral function in front of every model API.

Concept: a model is a function from a conversation to a reply. The loop
calls complete(); this module reads the model name, picks the adapter that
speaks that vendor's wire format, finds the key, and forwards the call.

Design rules:
  * "provider:model" picks the adapter (anthropic:claude-opus-5,
    ollama:qwen3:8b). A bare name is recognised by its prefix (claude-,
    gpt-, o1..o9), and anything else means Gemini, so older ZILL_MODEL
    values keep working.
  * Keys resolve lazily, on the first call. Building a Harness, reading
    model_info and running tests never need one.
  * Each adapter in zill/providers/ is the only code that knows its wire
    format; nothing here parses a vendor's JSON.

The provider contract (what every adapter honours):
  complete(model, system, messages, tools, base, key, on_text=None)
      -> {"text", "tool_calls", "usage"}; with on_text, stream and report text fragments
    messages: {"role": "user", "text"}
              {"role": "assistant", "text", "tool_calls": [{"id", "name", "args", ...}],
               "provider_data"?}
              {"role": "tool", "id", "name", "text"}
    tool_calls and an optional reply-level "provider_data" may carry
    adapter-private data (Gemini's signature, Claude's raw blocks); callers
    store them and hand them back untouched.
  model_info(model) -> capability dict; callers tolerate missing keys.
"""

import re

from . import credentials
from .providers import anthropic, gemini, openai_compat

# name: (adapter, base URL, key variable or None, default model or None)
PROVIDERS = {
    "gemini": (gemini, "https://generativelanguage.googleapis.com/v1beta",
               "GEMINI_API_KEY", "gemini-3.1-pro-preview"),
    "anthropic": (anthropic, "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "claude-opus-5"),
    "openai": (openai_compat, "https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-5"),
    "openrouter": (openai_compat, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None),
    "groq": (openai_compat, "https://api.groq.com/openai/v1", "GROQ_API_KEY", None),
    "deepseek": (openai_compat, "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
    "ollama": (openai_compat, "http://localhost:11434/v1", None, None),
    "lmstudio": (openai_compat, "http://localhost:1234/v1", None, None),
}
DEFAULT_MODEL = "gemini:gemini-3.1-pro-preview"
OPENAI_NAME = re.compile(r"(gpt-|chatgpt-|o\d)")


def resolve(model):
    """Split model into (provider name, adapter, base URL, key variable, model id)."""
    name, sep, model_id = model.partition(":")
    if not sep or name not in PROVIDERS:
        model_id = model
        name = ("anthropic" if model.startswith("claude") else
                "openai" if OPENAI_NAME.match(model) else "gemini")
    adapter, base, key_var, _ = PROVIDERS[name]
    return name, adapter, credentials.get("ZILL_BASE_URL") or base, key_var, model_id


def default_model():
    """ZILL_MODEL if set, else the default model of the first provider with a key."""
    chosen = credentials.get("ZILL_MODEL")
    if chosen:
        return chosen
    for name, (_, _, key_var, default) in PROVIDERS.items():
        if key_var and default and credentials.get(key_var):
            return f"{name}:{default}"
    return DEFAULT_MODEL


def missing_key(model):
    """Return the key variable model needs but lacks, or None."""
    name, _, _, key_var, _ = resolve(model)
    if key_var is None or credentials.get(key_var) or credentials.get("ZILL_API_KEY"):
        return None
    return key_var


def model_info(model):
    """Return what model supports, from its adapter; needs no key."""
    _, adapter, _, _, model_id = resolve(model)
    return adapter.model_info(model_id)


def complete(model, system, messages, tools, on_text=None):
    """Send one conversation to model's provider and return the neutral reply.

    With on_text, the reply streams and on_text receives each text fragment.
    """
    name, adapter, base, key_var, model_id = resolve(model)
    key = None
    if key_var:
        key = credentials.get(key_var) or credentials.get("ZILL_API_KEY")
        if not key:
            raise RuntimeError(f"No API key for {name}. Run `zill setup`, or set {key_var}.")
    return adapter.complete(model_id, system, messages, tools, base=base, key=key,
                            on_text=on_text)
