"""
extractors/llm.py — Tier 3: LLM-based extractor.

Only relevant when using the agora-code CLI directly.
When used as an MCP server with Claude Code / Cline / Cursor, Tier 3 is
not called — the coding agent is itself the LLM and understands the code.

Provider auto-detection (from env vars, in priority order):
  ANTHROPIC_API_KEY  →  see DEFAULT_MODELS["claude"]
  OPENAI_API_KEY     →  see DEFAULT_MODELS["openai"]
  GEMINI_API_KEY     →  see DEFAULT_MODELS["gemini"]

Override model:
  LLM_MODEL=claude-opus-4-5        ← any valid model name
  --llm-model claude-opus-4-5      ← CLI flag

Override provider:
  LLM_PROVIDER=openai              ← env var
  --llm-provider openai            ← CLI flag
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from agora_code.models import Param, Route, RouteCatalog

# ─── Update these when new models release ─────────────────────────────────────
DEFAULT_MODELS = {
    "claude": "claude-haiku-4-5",          # fast + cheap, good at structured JSON
    "openai": "gpt-4o-mini",               # good balance of speed/cost
    "gemini": "gemini-2.0-flash",          # fast, generous free tier
}
# ─────────────────────────────────────────────────────────────────

# Retry logic (optional dependency)
try:
    from tenacity import retry, stop_after_attempt, wait_exponential
    _HAS_TENACITY = True
except ImportError:
    _HAS_TENACITY = False
    # Fallback: no-op decorator
    def retry(*args, **kwargs):
        def decorator(func):
            return func
        return decorator
    stop_after_attempt = wait_exponential = lambda *a, **k: None

# File extensions to consider
_SUPPORTED_EXTS = {
    ".py", ".js", ".ts", ".mjs", ".jsx", ".tsx",
    ".rb", ".java", ".go", ".php", ".cs", ".rs",
}

# Heuristic: skip files unlikely to have routes
_ROUTE_KEYWORDS = {
    "route", "router", "controller", "handler", "endpoint",
    "get", "post", "put", "delete", "patch",
    "mapping", "blueprint", "resource",
}

_SYSTEM_PROMPT = """\
You are an API route extractor. Your job is to extract all HTTP API endpoints \
from source code files.

For each endpoint found, return a JSON object in this exact format:
{
  "routes": [
    {
      "method": "GET",
      "path": "/products/{id}",
      "description": "One sentence: what this endpoint does",
      "params": [
        {"name": "id", "type": "int", "required": true, "location": "path"},
        {"name": "include_deleted", "type": "bool", "required": false, "location": "query"}
      ]
    }
  ]
}

Rules:
- method: uppercase GET/POST/PUT/DELETE/PATCH only
- path: use {param} for path parameters
- type: str | int | float | bool | list | dict | any
- location: query | path | body | header
- If you see no routes, return {"routes": []}
- Return ONLY the JSON object, no explanation
"""


def _detect_provider() -> tuple[str, str]:
    """
    Auto-detect LLM provider from environment.
    Returns (provider_name, model_to_use).
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


async def extract(
    target: str,
    provider: str = "auto",
    model: Optional[str] = None,
    max_files: int = 50,
) -> RouteCatalog:
    """
    Use an LLM to extract routes from every source file.

    Args:
        target:    repo directory or single file path
        provider:  "auto" | "claude" | "openai" | "gemini"
                   "auto" picks from ANTHROPIC/OPENAI/GEMINI_API_KEY
        model:     override default model
        max_files: cap to avoid runaway costs
    """
    if provider in ("auto", "", None):
        detected_provider, detected_model = _detect_provider()
        if not detected_provider:
            raise RuntimeError(
                "No LLM provider available. Set one of:\n"
                "  ANTHROPIC_API_KEY  (Claude — recommended)\n"
                "  OPENAI_API_KEY     (GPT-4o-mini)\n"
                "  GEMINI_API_KEY     (Gemini Flash)\n"
                "Or set LLM_PROVIDER=claude|openai|gemini explicitly."
            )
        provider = detected_provider
        model = model or detected_model

    path = Path(target)
    files = [path] if path.is_file() else [
        f for f in path.rglob("*")
        if f.is_file()
        and f.suffix in _SUPPORTED_EXTS
        and not _is_excluded(f)
        and _looks_like_routes(f)
    ][:max_files]

    llm_fn = _get_llm(provider, model)
    routes: List[Route] = []

    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="ignore")[:4000]
            result = await llm_fn(source)
            routes.extend(_parse_llm_output(result))
        except Exception:
            continue

    return RouteCatalog(source=str(target), extractor=f"llm/{provider}", routes=routes)


# --------------------------------------------------------------------------- #
#  LLM backends                                                                #
# --------------------------------------------------------------------------- #

def _get_llm(provider: str, model: Optional[str] = None):
    provider = provider.lower()
    model = model or DEFAULT_MODELS.get(provider)
    if provider in ("claude", "anthropic"):
        return _make_claude_llm(model)
    elif provider == "openai":
        return _make_openai_llm(model)
    elif provider == "gemini":
        return _make_gemini_llm(model)
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider!r}. "
            "Use 'claude', 'openai', or 'gemini'."
        )


def _make_openai_llm(model: str):
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError("Install openai: pip install agora-code[openai]")
    client = AsyncOpenAI()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def call(source: str) -> str:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract routes from this file:\n\n```\n{source}\n```"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or "{}"

    return call


def _make_gemini_llm(model: str):
    try:
        import google.generativeai as genai
    except ImportError:
        raise ImportError("Install google-generativeai: pip install agora-code[gemini]")

    gen_model = genai.GenerativeModel(
        model_name=model,
        system_instruction=_SYSTEM_PROMPT,
        generation_config={"response_mime_type": "application/json"},
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def call(source: str) -> str:
        resp = await gen_model.generate_content_async(
            f"Extract routes from this file:\n\n```\n{source}\n```"
        )
        return resp.text or "{}"

    return call


def _make_claude_llm(model: str):
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "Install anthropic: pip install anthropic\n"
            "Or: pip install agora-code[claude]"
        )
    client = anthropic.AsyncAnthropic()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def call(source: str) -> str:
        resp = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Extract routes from this file:\n\n```\n{source}\n```",
            }],
        )
        return resp.content[0].text if resp.content else "{}"

    return call


# --------------------------------------------------------------------------- #
#  Output parsing                                                              #
# --------------------------------------------------------------------------- #

def _parse_llm_output(raw: str) -> List[Route]:
    try:
        data = json.loads(raw)
        routes = data.get("routes", [])
        result = []
        for r in routes:
            params = [
                Param(
                    name=p.get("name", "param"),
                    type=p.get("type", "any"),
                    required=p.get("required", False),
                    location=p.get("location", "query"),
                    description=p.get("description", ""),
                )
                for p in r.get("params", [])
            ]
            result.append(Route(
                method=r.get("method", "GET").upper(),
                path=r.get("path", "/"),
                description=r.get("description", ""),
                params=params,
            ))
        return result
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _looks_like_routes(path: Path) -> bool:
    """Quick heuristic: does this file likely contain route definitions?"""
    name_lower = path.stem.lower()
    if any(kw in name_lower for kw in _ROUTE_KEYWORDS):
        return True
    try:
        sample = path.read_text(encoding="utf-8", errors="ignore")[:500].lower()
        return any(kw in sample for kw in _ROUTE_KEYWORDS)
    except Exception:
        return False


def _is_excluded(path: Path) -> bool:
    excluded = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", "vendor", "target",
    }
    return any(part in excluded for part in path.parts)