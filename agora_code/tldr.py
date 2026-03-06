"""
tldr.py — AST-based route catalog compression for agora-code.

Compresses discovered API routes into a compact representation before
injecting into coding agents (Claude, Cursor, Cline). No LLM required —
pure structural extraction.

Compression levels:
  index   — route names only (~50 tokens per 10 routes)
  summary — names + one-line descriptions (default, ~200 tokens)
  detail  — names + descriptions + all params (~500 tokens)
  full    — uncompressed JSON

Token budget auto-selection:
  estimate_tokens(text) uses the 4 chars ≈ 1 token heuristic.
  auto_level(routes) picks the highest-detail level under 2000 tokens.

Inspired by FlowGuardian's tldr.py (MIT) — adapted for API routes
rather than session context.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agora_code.models import Route, RouteCatalog

# Compression level ordering — lower index = more compressed
LEVELS = ["index", "summary", "detail", "full"]

# Token budget for auto_level()
DEFAULT_TOKEN_BUDGET = 2000


# --------------------------------------------------------------------------- #
#  Token estimation                                                            #
# --------------------------------------------------------------------------- #

def estimate_tokens(text: str) -> int:
    """
    Estimate token count using the 4 chars ≈ 1 token heuristic.
    Good enough for budget decisions without calling a tokenizer.
    """
    return max(1, len(text) // 4)


# --------------------------------------------------------------------------- #
#  Route catalog compression                                                   #
# --------------------------------------------------------------------------- #

def summarize_routes(
    routes: List[Route],
    level: str = "summary",
    source: Optional[str] = None,
) -> str:
    """
    Compress a list of routes to the given level.

    Args:
        routes: List of Route objects from the scanner
        level:  'index' | 'summary' | 'detail' | 'full'
        source: Optional source label (e.g. 'openapi', 'ast', 'llm')

    Returns:
        Compressed string representation of the route catalog.
    """
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}, got {level!r}")

    if level == "full":
        return _full(routes, source)
    elif level == "index":
        return _index(routes, source)
    elif level == "summary":
        return _summary(routes, source)
    elif level == "detail":
        return _detail(routes, source)

    return _summary(routes, source)  # fallback


def auto_level(
    routes: List[Route],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    source: Optional[str] = None,
) -> tuple[str, str]:
    """
    Automatically pick the highest-detail level that fits within token_budget.

    Returns:
        (level, compressed_text) — the level chosen and the resulting text.
    """
    # Try from most detailed to least (excluding 'full' which is always large)
    for level in ["detail", "summary", "index"]:
        text = summarize_routes(routes, level=level, source=source)
        if estimate_tokens(text) <= token_budget:
            return level, text

    # Last resort: index
    text = summarize_routes(routes, level="index", source=source)
    return "index", text


# --------------------------------------------------------------------------- #
#  Level implementations                                                       #
# --------------------------------------------------------------------------- #

def _header(count: int, source: Optional[str]) -> str:
    src_str = f" [{source}]" if source else ""
    return f"## API Routes{src_str} ({count} endpoints)\n"


def _index(routes: List[Route], source: Optional[str]) -> str:
    """Level 0: just METHOD /path — one line each."""
    lines = [_header(len(routes), source)]
    for r in routes:
        lines.append(f"- {r.method} {r.path}")
    return "\n".join(lines)


def _summary(routes: List[Route], source: Optional[str]) -> str:
    """Level 1: METHOD /path — description (one line each)."""
    lines = [_header(len(routes), source)]
    for r in routes:
        desc = _short_desc(r)
        if desc:
            lines.append(f"- {r.method} {r.path} — {desc}")
        else:
            lines.append(f"- {r.method} {r.path}")
    return "\n".join(lines)


def _detail(routes: List[Route], source: Optional[str]) -> str:
    """Level 2: METHOD /path + description + params."""
    lines = [_header(len(routes), source)]
    for r in routes:
        desc = _short_desc(r)
        header = f"- {r.method} {r.path}"
        if desc:
            header += f" — {desc}"
        lines.append(header)
        if r.params:
            param_parts = []
            for p in r.params:
                required = " (required)" if p.required else ""
                param_parts.append(f"{p.name}: {p.type}{required}")
            lines.append(f"  params: {', '.join(param_parts)}")
    return "\n".join(lines)


def _full(routes: List[Route], source: Optional[str]) -> str:
    """Level 3: raw JSON dump — uncompressed."""
    data = {
        "source": source,
        "count": len(routes),
        "routes": [_route_to_dict(r) for r in routes],
    }
    return json.dumps(data, indent=2)


# --------------------------------------------------------------------------- #
#  Catalog-level compression                                                   #
# --------------------------------------------------------------------------- #

def compress_catalog(
    catalog: RouteCatalog,
    level: str = "summary",
) -> str:
    """
    Compress a full RouteCatalog to the given level.

    Convenience wrapper around summarize_routes that reads source from catalog.
    """
    return summarize_routes(catalog.routes, level=level, source=catalog.source)


def compress_catalog_auto(
    catalog: RouteCatalog,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> tuple[str, str]:
    """
    Auto-pick compression level for a RouteCatalog.

    Returns:
        (level, compressed_text)
    """
    return auto_level(catalog.routes, token_budget=token_budget, source=catalog.source)


# --------------------------------------------------------------------------- #
#  Quality measurement                                                         #
# --------------------------------------------------------------------------- #

def measure_compression(routes: List[Route], level: str) -> dict:
    """
    Measure compression ratio for a given level vs full JSON.

    Returns:
        {
            "level": level,
            "original_tokens": int,
            "compressed_tokens": int,
            "reduction_pct": float,
        }
    """
    full_text = _full(routes, source=None)
    compressed_text = summarize_routes(routes, level=level)

    original = estimate_tokens(full_text)
    compressed = estimate_tokens(compressed_text)
    reduction = round((1 - compressed / original) * 100, 1) if original > 0 else 0.0

    return {
        "level": level,
        "original_tokens": original,
        "compressed_tokens": compressed,
        "reduction_pct": reduction,
    }


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _short_desc(route: Route) -> str:
    """Get a short one-line description from a route."""
    desc = getattr(route, "description", None) or ""
    if not desc:
        return ""
    # Truncate to first sentence or 80 chars
    first_line = desc.split("\n")[0].strip()
    if len(first_line) > 80:
        return first_line[:77] + "..."
    return first_line


def _route_to_dict(route: Route) -> Dict[str, Any]:
    """Convert a Route to a plain dict for JSON serialization."""
    return {
        "method": route.method,
        "path": route.path,
        "description": getattr(route, "description", None),
        "params": [
            {
                "name": p.name,
                "type": p.type,
                "location": p.location,
                "required": p.required,
                "description": getattr(p, "description", None),
            }
            for p in (route.params or [])
        ],
    }
