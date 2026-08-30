"""
LLM backend configuration -- which model generates predicates in
engine/l4_llm_generation.py, and where it runs.

Two backends:
  - "local": Qwen2.5-3B-Instruct on local GPU via outlines (the original,
    default path -- $0 marginal cost, no API key, no network call).
  - "openrouter": a hosted model via OpenRouter's OpenAI-compatible API.
    Requires an API key and costs real money per call, in exchange for not
    needing a local CUDA GPU (see README's documented torch/triton-windows
    pain) -- a portability tradeoff, not a strict upgrade, which is why this
    stays opt-in rather than replacing the local default.

Persisted to a single local JSON file, gitignored, never committed and
never echoed back to the frontend in plaintext once saved (the API only
ever reports whether a key is set, not its value). This is a local
single-user dev tool serving localhost only -- plaintext-on-disk is an
accepted tradeoff here, the same pattern many CLIs use for a local config
file; it would NOT be appropriate for a multi-user or internet-facing
deployment.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / ".llm_config.json"

DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
VALID_BACKENDS = {"local", "openrouter"}


def get_llm_config() -> dict:
    """Returns the live config. `api_key` is included here because this
    function is for SERVER-SIDE use (feeding the actual API call) -- the
    api/main.py endpoint that exposes this to the browser strips it before
    responding."""
    if not CONFIG_PATH.exists():
        return {"backend": "local", "api_key": None, "openrouter_model": DEFAULT_OPENROUTER_MODEL}
    data = json.loads(CONFIG_PATH.read_text())
    data.setdefault("backend", "local")
    data.setdefault("api_key", None)
    data.setdefault("openrouter_model", DEFAULT_OPENROUTER_MODEL)
    return data


def set_llm_config(backend: str | None = None, api_key: str | None = None, openrouter_model: str | None = None) -> dict:
    """Merges into the existing config rather than overwriting -- e.g.
    switching backend back to "local" shouldn't discard a previously-saved
    OpenRouter key, so re-enabling "openrouter" later doesn't ask again."""
    current = get_llm_config()
    if backend is not None:
        if backend not in VALID_BACKENDS:
            raise ValueError(f"Unknown backend '{backend}', must be one of {sorted(VALID_BACKENDS)}.")
        current["backend"] = backend
    if api_key is not None:
        current["api_key"] = api_key
    if openrouter_model is not None:
        current["openrouter_model"] = openrouter_model
    CONFIG_PATH.write_text(json.dumps(current, indent=2))
    return current


def public_llm_config() -> dict:
    """The browser-safe view: never the key itself, just whether one is set."""
    cfg = get_llm_config()
    return {
        "backend": cfg["backend"],
        "has_api_key": bool(cfg.get("api_key")),
        "openrouter_model": cfg["openrouter_model"],
    }
