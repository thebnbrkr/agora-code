"""
llm_provider.py — provider-agnostic LLM auto-detection, shared across agora-code.

Used by memory-side features that need their own model call because no coding
agent is in the loop at call time (e.g. cli.py's _llm_change_note(), used by
`track-diff` when it runs from a detached git/shell hook).

When agora-code runs as an MCP server inside Claude Code, Cursor, or any other
coding agent, none of this is needed — the coding agent is itself the LLM.
This module exists only for the standalone-process case.

Provider auto-detection (from env vars, in priority order):
  ANTHROPIC_API_KEY  →  see DEFAULT_MODELS["claude"]
  OPENAI_API_KEY     →  see DEFAULT_MODELS["openai"]
  GEMINI_API_KEY     →  see DEFAULT_MODELS["gemini"]

Override model:
  LLM_MODEL=claude-opus-4-5        ← any valid model name
Override provider:
  LLM_PROVIDER=openai              ← env var
"""

from __future__ import annotations

import os

# ─── Update these when new models release ─────────────────────────────────────
DEFAULT_MODELS = {
    "claude": "claude-haiku-4-5",          # fast + cheap, good at structured JSON
    "openai": "gpt-4o-mini",               # good balance of speed/cost
    "gemini": "gemini-2.0-flash",          # fast, generous free tier
}
# ─────────────────────────────────────────────────────────────────


def _detect_provider() -> tuple[str, str]:
    """
    Auto-detect LLM provider from environment.
    Returns (provider_name, model_to_use), or ("", "") if none configured.
    """
    env_provider = os.environ.get("LLM_PROVIDER", "").lower()
    env_model    = os.environ.get("LLM_MODEL", "")

    # Explicit provider override takes priority
    if env_provider in ("claude", "anthropic"):
        return "claude", env_model or DEFAULT_MODELS["claude"]
    if env_provider == "openai":
        return "openai", env_model or DEFAULT_MODELS["openai"]
    if env_provider == "gemini":
        return "gemini", env_model or DEFAULT_MODELS["gemini"]

    # Auto-detect from API keys
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude", env_model or DEFAULT_MODELS["claude"]
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", env_model or DEFAULT_MODELS["openai"]
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini", env_model or DEFAULT_MODELS["gemini"]

    return "", ""   # no provider available


def is_available() -> bool:
    """True if any LLM provider is configured."""
    provider, _ = _detect_provider()
    return bool(provider)
