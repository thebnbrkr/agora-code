"""
extractors/llm.py — Tier 3: LLM-based extractor.

Works for any language, any framework. Costs a small amount per repo.
Requires: pip install agora-code[openai] or agora-code[gemini]

The LLM reads each source file and returns structured route info.
Skips files that don't look like they contain routes (heuristic filter).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from agora_code.models import Param, Route, RouteCatalog

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


def can_handle(target: str) -> bool:
    """Can always handle — but requires API key."""
    return True


async def extract(
    target: str,
    provider: str = "openai",
    model: str = None,
    max_files: int = 50,
) -> RouteCatalog:
    """
    Use an LLM to extract routes from every source file.

    Args:
        target:    repo directory or single file path
        provider:  "openai" | "gemini"
        model:     override default model
        max_files: cap to avoid runaway costs
    """
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
            source = f.read_text(encoding="utf-8", errors="ignore")[:4000]  # cap tokens
            result = await llm_fn(source)
            routes.extend(_parse_llm_output(result))
        except Exception:
            continue  # don't fail entire scan on one file

    return RouteCatalog(source=str(target), extractor="llm", routes=routes)


# --------------------------------------------------------------------------- #
#  LLM backends                                                                #
# --------------------------------------------------------------------------- #

def _get_llm(provider: str, model: str = None):
    if provider == "openai":
        return _make_openai_llm(model or "gpt-4o-mini")
    elif provider == "gemini":
        return _make_gemini_llm(model or "gemini-1.5-flash")
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Choose 'openai' or 'gemini'")


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