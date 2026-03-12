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

Context compression for API routes and session state.
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


# --------------------------------------------------------------------------- #
#  Session state compression                                                   #
#                                                                              #
#  How we save tokens: instead of dumping full session JSON (~3 000 tokens)   #
#  into Claude's context on startup, we compress to fit the budget.           #
#                                                                              #
#  index   ~50 t   goal + endpoint list                                        #
#  summary ~200 t  + hypothesis + top discoveries + next steps  (DEFAULT)     #
#  detail  ~500 t  + all attempts, decisions, blockers                         #
#  full    raw JSON                                                            #
# --------------------------------------------------------------------------- #

SESSION_DEFAULT_BUDGET = 2000   # tokens allowed for session context


def compress_session(session: dict, level: str = "summary") -> str:
    """
    Compress a session dict for injection into the AI assistant's context.

    Args:
        session: Session dict (from session.py)
        level:   'index' | 'summary' | 'detail' | 'full'

    Returns:
        Compressed string ready to inject — not the raw JSON.
    """
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}, got {level!r}")

    if level == "index":
        return _session_index(session)
    elif level == "summary":
        return _session_summary(session)
    elif level == "detail":
        return _session_detail(session)
    else:
        return json.dumps(session, indent=2)


def auto_compress_session(
    session: dict,
    token_budget: int = SESSION_DEFAULT_BUDGET,
) -> str:
    """
    Auto-pick highest-detail session compression that fits token_budget.
    Returns the compressed text.
    """
    for level in ["detail", "summary", "index"]:
        text = compress_session(session, level)
        if estimate_tokens(text) <= token_budget:
            return text
    return compress_session(session, "index")


def session_restored_banner(session: dict, token_budget: int = SESSION_DEFAULT_BUDGET) -> str:
    """
    Generate the ═══ banner shown to the AI on MCP server startup.
    Injected once so the AI knows exactly where you left off.
    """
    compressed = auto_compress_session(session, token_budget)
    age = _session_age_str(session)
    age_line = f"  Last active: {age}\n" if age else ""

    return (
        "═" * 63 + "\n"
        f"🔄  SESSION RESTORED: {session.get('session_id', 'unknown')}\n"
        f"{age_line}"
        "\n"
        f"{compressed}\n"
        "\n"
        "Ready to continue where you left off.\n"
        + "═" * 63
    )


# ─── session level implementations ──────────────────────────────────────────

def _session_index(session: dict) -> str:
    """~50 tokens: goal + endpoint list."""
    goal = session.get("goal") or "No goal set"
    endpoints = session.get("endpoints_tested", [])
    ep_str = ", ".join(f"{e['method']} {e['path']}" for e in endpoints[:8])
    suffix = f" +{len(endpoints)-8} more" if len(endpoints) > 8 else ""
    return f"Goal: {goal}\nEndpoints: {ep_str or '(none)'}{suffix}"


def _session_summary(session: dict) -> str:
    """~200 tokens: goal + hypothesis + discoveries + next steps."""
    lines: list[str] = []

    if session.get("goal"):
        lines.append(f"GOAL: {session['goal']}")
    if session.get("status", "in_progress") != "in_progress":
        lines.append(f"STATUS: {session['status']}")
    if session.get("hypothesis"):
        lines.append(f"HYPOTHESIS: {session['hypothesis']}")
    if session.get("current_action"):
        lines.append(f"NOW: {session['current_action']}")

    discoveries = session.get("discoveries", [])
    if discoveries:
        lines.append("WHAT YOU DISCOVERED:")
        for d in discoveries[:4]:
            mark = "  ✓" if d.get("confidence") == "confirmed" else "  ~"
            lines.append(f"{mark} {d['finding']}")
        if len(discoveries) > 4:
            lines.append(f"  … +{len(discoveries)-4} more")

    next_steps = session.get("next_steps", [])
    if next_steps:
        lines.append("NEXT STEPS:")
        for step in next_steps[:3]:
            lines.append(f"  → {step}")

    endpoints = session.get("endpoints_tested", [])
    if endpoints:
        lines.append("ENDPOINTS:")
        for ep in endpoints[:5]:
            a = ep.get("attempts", 0)
            s = ep.get("successes", 0)
            lines.append(f"  • {ep['method']} {ep['path']}  ({s}/{a} ok)")

    blockers = session.get("blockers", [])
    for b in blockers[:2]:
        desc = b if isinstance(b, str) else b.get("description", str(b))
        lines.append(f"  ⚠️  {desc}")

    return "\n".join(lines)


def _session_detail(session: dict) -> str:
    """~500 tokens: summary + files changed + decisions + full endpoint table."""
    lines = [_session_summary(session)]

    files = session.get("files_changed", [])
    if files:
        lines.append("\nFILES CHANGED:")
        for f in files[:10]:
            if isinstance(f, dict):
                fname = f.get("file", "")
                what = f.get("what", "")
                lines.append(f"  • {fname} — {what}" if what else f"  • {fname}")
            else:
                lines.append(f"  • {f}")
        if len(files) > 10:
            lines.append(f"  … +{len(files) - 10} more")

    decisions = session.get("decisions_made", [])
    if decisions:
        lines.append("\nDECISIONS MADE:")
        for d in decisions:
            lines.append(f"  – {d}")

    endpoints = session.get("endpoints_tested", [])
    if endpoints:
        lines.append("\nFULL ENDPOINT STATUS:")
        for ep in endpoints:
            a = ep.get("attempts", 0)
            s = ep.get("successes", 0)
            f = ep.get("failures", 0)
            lines.append(f"  {ep['method']} {ep['path']}  {s}/{a} ok, {f} fail")
            if ep.get("last_error"):
                lines.append(f"    last error: {ep['last_error']}")
            if ep.get("working_parameters"):
                lines.append(f"    working params: {ep['working_parameters']}")
            fails = ep.get("failing_parameters", [])
            if fails:
                extra = f" +{len(fails)-1} more" if len(fails) > 1 else ""
                lines.append(f"    failing params: {fails[0]}{extra}")

    return "\n".join(lines)


def _session_age_str(session: dict) -> str:
    """'2 hours ago', '3 days ago', etc."""
    try:
        from datetime import datetime, timezone
        last = session.get("last_active", "")
        if not last:
            return ""
        dt = datetime.fromisoformat(last)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds()/60)} minutes ago"
        elif hours < 24:
            return f"{int(hours)} hours ago"
        else:
            return f"{int(hours/24)} days ago"
    except Exception:
        return ""
