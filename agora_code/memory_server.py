"""
memory_server.py — Project-agnostic MCP server for day-to-day coding.

Exposes session memory tools to any MCP client (Antigravity, Claude Desktop,
Cline, Cursor) so you get persistent context across all your projects.

No target directory needed. No API server needed.
Just run it and it gives your AI assistant:
  - Your current session context (what you're working on)
  - Ability to save checkpoints
  - Learning storage and recall
  - Context injection

Add to Antigravity / Claude Desktop config:
{
  "mcpServers": {
    "agora-memory": {
      "command": "agora-code",
      "args": ["memory-server"],
      "env": {
        "OPENAI_API_KEY": "optional — for semantic recall"
      }
    }
  }
}

Then ask your AI: "What's my current session?" or just start coding —
it'll automatically know your goal, hypothesis, and last discoveries.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
#  Tool definitions                                                            #
# --------------------------------------------------------------------------- #

_TOOLS = [
    {
        "name": "get_session_context",
        "description": (
            "Get the current coding session context — what the developer is working on, "
            "their hypothesis, recent discoveries, next steps, and blockers. "
            "USE THIS WHEN: starting a new conversation, or when you need to know what "
            "the developer was doing before this session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["index", "summary", "detail", "full"],
                    "default": "detail",
                    "description": "How much detail to include. detail (~500 tokens) is the default for AI agents; summary (~200 tokens) for quick checks."
                }
            }
        }
    },
    {
        "name": "save_checkpoint",
        "description": (
            "Save the current state of the coding session. Call this when the developer "
            "completes a meaningful step, sets a new goal, or you want to remember their "
            "current hypothesis. "
            "USE THIS WHEN: developer asks to save progress, or after solving something important."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal":        {"type": "string", "description": "What the developer is trying to accomplish"},
                "hypothesis":  {"type": "string", "description": "Current working theory or approach"},
                "action":      {"type": "string", "description": "What they're doing right now"},
                "context":     {"type": "string", "description": "Free-text project notes (stack, constraints, etc.)"},
                "next_steps":  {"type": "array", "items": {"type": "string"}, "description": "What to do next"},
                "blockers":    {"type": "array", "items": {"type": "string"}, "description": "Current blockers"},
                "files_changed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files edited, e.g. ['auth.py:added retry logic', 'tests/test_auth.py']"
                }
            }
        }
    },
    {
        "name": "store_learning",
        "description": (
            "Store a permanent finding or insight in long-term memory. "
            "These persist across sessions and projects, searchable later via recall_learnings. "
            "USE THIS WHEN: developer discovers something non-obvious, a gotcha, or a pattern "
            "worth remembering (e.g. 'API rejects + in emails', 'auth token expires in 15min')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "finding": {"type": "string", "description": "What was learned"},
                "evidence": {"type": "string", "description": "How this was discovered"},
                "confidence": {
                    "type": "string",
                    "enum": ["confirmed", "likely", "hypothesis"],
                    "default": "confirmed"
                },
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Topics for search"}
            },
            "required": ["finding"]
        }
    },
    {
        "name": "recall_learnings",
        "description": (
            "Search the long-term memory for past findings and insights. "
            "Returns the most relevant results — semantic search if embeddings configured, "
            "keyword search otherwise. "
            "USE THIS WHEN: working on something that might have been tackled before, "
            "or when the developer asks 'what did we learn about X?'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "limit": {"type": "integer", "default": 5, "description": "Max results"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "complete_session",
        "description": (
            "Archive the current session with a summary. Call this when a task is done. "
            "The session gets stored in long-term memory so it can be recalled later."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What was accomplished"},
                "outcome": {
                    "type": "string",
                    "enum": ["success", "partial", "abandoned"],
                    "default": "success"
                }
            }
        }
    },
    {
        "name": "get_memory_stats",
        "description": "Get stats about stored memory: session count, learning count, and search mode.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "list_sessions",
        "description": (
            "List all past coding sessions stored in memory, with their goals, status, and branch. "
            "USE THIS WHEN: developer asks 'what have I been working on?', or you need to find a specific "
            "past session to restore context from. Returns session IDs you can reference with get_session_context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "Max sessions to return"},
                "branch": {"type": "string", "description": "Filter by git branch name (optional)"}
            }
        }
    },
    {
        "name": "store_team_learning",
        "description": (
            "Store a finding in shared team memory. Team learnings are visible to all agents and "
            "teammates querying team namespace. Ideal for cross-project gotchas, API conventions, "
            "or patterns multiple agents should know. "
            "USE THIS WHEN: discovery is broadly applicable, not project-specific."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "finding": {"type": "string", "description": "What was learned"},
                "evidence": {"type": "string", "description": "How this was discovered"},
                "confidence": {
                    "type": "string",
                    "enum": ["confirmed", "likely", "hypothesis"],
                    "default": "confirmed"
                },
                "tags": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["finding"]
        }
    },
    {
        "name": "recall_team",
        "description": (
            "Search the shared team knowledge base. Returns findings stored by any agent or teammate "
            "in the team namespace. "
            "USE THIS WHEN: developer asks 'has anyone figured out X?', looking for shared conventions, "
            "or troubleshooting something others may have hit before."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for in team knowledge"},
                "limit": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        }
    },
    {
        "name": "recall_file_history",
        "description": (
            "Return the tracked change history for a specific file — what was changed, when, "
            "by which session, on which branch. Compact summaries, not raw diffs. "
            "USE THIS WHEN: starting work on a file and want to know what changed recently, "
            "debugging regressions, or understanding why a function looks the way it does."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Relative path to the file, e.g. 'agora_code/auth.py'"},
                "limit": {"type": "integer", "default": 10}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "get_file_symbols",
        "description": (
            "Return one-liner descriptions of every function/class in a file — WITHOUT reading the file. "
            "Each entry has: symbol_name, symbol_type, start_line, end_line, signature, note. "
            "USE THIS INSTEAD OF reading a file when you only need to know what functions exist "
            "or what they do. Saves ~97% of tokens vs reading the full file. "
            "Then use the start_line/end_line to Read only the specific function you need."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file, e.g. 'agora_code/cli.py'"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "search_symbols",
        "description": (
            "Search for functions/classes across the entire codebase by name, signature, or description. "
            "Returns matching symbols with file path, line numbers, and one-liner notes. "
            "USE THIS WHEN: looking for where a function is defined, finding all functions that "
            "handle a specific concern (e.g. 'authentication', 'database'), or exploring a codebase."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Function name, concept, or description to search for"
                },
                "symbol_type": {
                    "type": "string",
                    "enum": ["function", "method", "class"],
                    "description": "Filter by symbol type (optional)"
                },
                "limit": {"type": "integer", "default": 10}
            },
            "required": ["query"]
        }
    }
]


# --------------------------------------------------------------------------- #
#  Tool handlers                                                               #
# --------------------------------------------------------------------------- #

async def _handle_get_session_context(params: dict) -> str:
    from agora_code.session import (
        load_session, update_session, _build_recalled_context,
    )
    from agora_code.compress import session_restored_banner

    session = load_session()
    if not session:
        # No active session — surface DB context so agent isn't flying blind
        recalled = _build_recalled_context()
        if recalled:
            return f"No active session. Past context from memory:\n\n{recalled}"
        return "No active session. Start one with save_checkpoint(goal='...')."

    # Auto-populate context field once per session from DB if empty
    if not session.get("context"):
        recalled = _build_recalled_context()
        if recalled:
            session = update_session({"context": recalled})

    level = params.get("level", "detail")  # default: detail for AI agents
    if level == "full":
        return json.dumps(session, indent=2)

    return session_restored_banner(session, token_budget=3000)


async def _handle_save_checkpoint(params: dict) -> str:
    from agora_code.session import update_session

    updates: dict = {}
    if params.get("goal"):        updates["goal"] = params["goal"]
    if params.get("hypothesis"):  updates["hypothesis"] = params["hypothesis"]
    if params.get("action"):      updates["current_action"] = params["action"]
    if params.get("context"):     updates["context"] = params["context"]
    if params.get("next_steps"):  updates["next_steps"] = params["next_steps"]
    if params.get("blockers"):    updates["blockers"] = params["blockers"]
    if params.get("files_changed"):
        files = []
        for f in params["files_changed"]:
            if ":" in f:
                fname, what = f.split(":", 1)
                files.append({"file": fname.strip(), "what": what.strip()})
            else:
                files.append({"file": f.strip(), "what": ""})
        updates["files_changed"] = files

    session = update_session(updates)
    return f"Session saved: {session['session_id']} — Goal: {session.get('goal', '(none)')}"


async def _handle_store_learning(params: dict, namespace: str = "personal") -> str:
    from agora_code.vector_store import get_store
    from agora_code.embeddings import get_embedding
    from agora_code.session import (
        load_session,
        _get_git_branch,
        _get_project_id,
        _get_uncommitted_files,
    )

    finding = params.get("finding", "")
    embedding = get_embedding(finding)
    store = get_store()

    # Auto-capture git context at the moment of storing
    branch = _get_git_branch()
    project_id = _get_project_id()
    files = _get_uncommitted_files()
    # Also pull files from active session if available
    session = load_session()
    if session and not files:
        files = [f.get("file", f) if isinstance(f, dict) else f
                 for f in session.get("files_changed", [])]

    lid = store.store_learning(
        finding,
        evidence=params.get("evidence"),
        confidence=params.get("confidence", "confirmed"),
        tags=params.get("tags", []),
        embedding=embedding,
        branch=branch,
        files=files,
        namespace=namespace,
        project_id=project_id,
    )
    scope = " [team]" if namespace == "team" else ""
    return f"Stored{scope}: {finding[:80]}{'...' if len(finding) > 80 else ''}"


def _apply_recency_scoring(results: list, current_branch: Optional[str] = None,
                           current_files: Optional[List[str]] = None) -> list:
    """
    Re-rank results by blending relevance rank with recency, confidence,
    branch-match, and file-overlap scoring.

    Scoring weights:
      Rank score:       1.0 / (rank + 1)        — FTS/semantic relevance
      Recency boost:    0.0–0.4                  — exponential decay, 48h half-life
      Confidence boost: confirmed +0.2, likely +0.1
      Branch score:     exact +0.30, same-prefix +0.15 (gradient, beats FG's binary)
      File overlap:     0.2 * min(overlap/3, 1.0)  — matches FlowGuardian
    """
    from datetime import datetime, timezone
    import math

    now = datetime.now(timezone.utc)
    current_files_set = set(current_files or [])

    scored = []
    for rank, r in enumerate(results):
        rank_score = 1.0 / (rank + 1)

        # Recency boost
        recency_boost = 0.0
        ts = r.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_hours = (now - dt).total_seconds() / 3600
                recency_boost = math.exp(-age_hours / 48)
            except Exception:
                pass

        # Confidence boost
        conf_boost = {"confirmed": 0.2, "likely": 0.1, "hypothesis": 0.0}.get(
            r.get("confidence", "confirmed"), 0.0
        )

        # Branch-match boost (gradient — better than FlowGuardian's binary)
        branch_boost = 0.0
        stored_branch = r.get("branch")
        if current_branch and stored_branch:
            if current_branch == stored_branch:
                branch_boost = 0.30  # exact same branch
            elif (current_branch.split("/")[0] == stored_branch.split("/")[0]
                  and "/" in current_branch):
                branch_boost = 0.15  # same branch type (feat/*, fix/*, chore/*)

        # File-overlap boost (proportional, capped at 3 matches = max 0.2)
        file_boost = 0.0
        stored_files = set(r.get("files") or [])
        if current_files_set and stored_files:
            overlap = len(current_files_set & stored_files)
            if overlap > 0:
                file_boost = 0.2 * min(overlap / 3, 1.0)

        final_score = (rank_score
                       + 0.4 * recency_boost
                       + conf_boost
                       + branch_boost
                       + file_boost)
        scored.append((final_score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


async def _handle_recall_learnings(params: dict, namespace: str = "personal") -> str:
    from agora_code.vector_store import get_store
    from agora_code.embeddings import get_embedding, is_available
    from agora_code.session import load_session, _get_git_branch, _get_uncommitted_files

    raw_query = params.get("query", "")
    limit = int(params.get("limit", 5))
    store = get_store()

    # Context-aware query enrichment from active session
    current_branch = _get_git_branch()
    current_files = _get_uncommitted_files()
    session = load_session()
    enriched_query = raw_query
    if session:
        parts = [raw_query]
        if session.get("goal"):
            parts.append(f"Goal: {session['goal']}")
        if current_branch:
            parts.append(f"Branch: {current_branch}")
        if current_files:
            parts.append(f"Working on: {', '.join(current_files[:5])}")
        enriched_query = ". ".join(p for p in parts if p)

    if is_available():
        emb = get_embedding(enriched_query)
        if emb:
            results = store.search_learnings_semantic(emb, k=limit * 2, namespace=namespace)
        else:
            results = store.search_learnings_keyword(raw_query, k=limit * 2, namespace=namespace)
    else:
        results = store.search_learnings_keyword(raw_query, k=limit * 2, namespace=namespace)

    # Apply full scoring: recency + confidence + branch-match + file-overlap
    results = _apply_recency_scoring(results,
                                     current_branch=current_branch,
                                     current_files=current_files)[:limit]

    if not results:
        ns_str = " in team memory" if namespace == "team" else ""
        return f"No learnings found for '{raw_query}'{ns_str}. Store one with store_learning()."

    lines = [f"Found {len(results)} learning(s) for '{raw_query}':\n"]
    for i, r in enumerate(results, 1):
        conf = {"confirmed": "✓", "likely": "~", "hypothesis": "?"}.get(r.get("confidence", ""), "")
        lines.append(f"{i}. {conf} {r['finding']}")
        if r.get("evidence"):
            lines.append(f"   Evidence: {r['evidence']}")
        if r.get("tags"):
            lines.append(f"   Tags: {', '.join(r['tags'])}")
        if r.get("branch"):
            branch_note = " ← same branch" if r["branch"] == current_branch else f" (from {r['branch']})"
            lines.append(f"   Branch: {r['branch']}{branch_note}")
    return "\n".join(lines)


async def _handle_complete_session(params: dict) -> str:
    from agora_code.session import archive_session

    session = archive_session(
        summary=params.get("summary"),
        outcome=params.get("outcome", "success"),
    )
    return f"Session '{session.get('session_id')}' archived. Summary: {params.get('summary', '(none)')}"


async def _handle_get_memory_stats(params: dict) -> str:
    from agora_code.vector_store import get_store
    from agora_code.embeddings import is_available, provider_info

    store = get_store()
    stats = store.get_stats()
    pinfo = provider_info()
    search_mode = f"semantic ({pinfo['provider']})" if is_available() else "keyword (FTS5)"

    return (
        f"Memory stats:\n"
        f"  Sessions archived: {stats.get('sessions', 0)}\n"
        f"  Learnings stored:  {stats.get('learnings', 0)}\n"
        f"  API calls logged:  {stats.get('api_calls', 0)}\n"
        f"  Search mode:       {search_mode}\n"
        f"  DB location:       {store.db_path}"
    )


async def _handle_list_sessions(params: dict) -> str:
    from agora_code.vector_store import get_store

    limit = int(params.get("limit", 20))
    branch_filter = params.get("branch")
    store = get_store()
    sessions = store.list_sessions(limit=limit)

    if branch_filter:
        sessions = [s for s in sessions if s.get("branch") == branch_filter or
                    (not s.get("branch") and not branch_filter)]

    if not sessions:
        return "No sessions found in memory."

    lines = [f"Found {len(sessions)} session(s):\n"]
    for s in sessions:
        status_icon = {"in_progress": "🔄", "complete": "✅", "abandoned": "❌"}.get(
            s.get("status", ""), "📋"
        )
        branch_str = f" [{s['branch']}]" if s.get("branch") else ""
        lines.append(f"{status_icon} {s['session_id']}{branch_str}")
        if s.get("goal"):
            lines.append(f"   Goal: {s['goal']}")
        lines.append(f"   Last active: {s.get('last_active', 'unknown')[:19]}")
    return "\n".join(lines)


async def _handle_store_team_learning(params: dict) -> str:
    return await _handle_store_learning(params, namespace="team")


async def _handle_recall_team(params: dict) -> str:
    return await _handle_recall_learnings(params, namespace="team")


async def _handle_recall_file_history(params: dict) -> str:
    from agora_code.vector_store import get_store
    file_path = params.get("file_path", "").strip()
    if not file_path:
        return "Error: file_path is required."
    limit = int(params.get("limit", 10))
    history = get_store().get_file_history(file_path, limit=limit)
    if not history:
        return (
            f"No tracked changes for '{file_path}'. "
            "Changes are captured automatically by the PostToolUse hook when files are edited."
        )
    lines = [f"Change history for {file_path} ({len(history)} entries):"]
    for e in history:
        ts = e.get("timestamp", "")[:16]
        branch = f" [{e['branch']}]" if e.get("branch") else ""
        sha = f" @{e['commit_sha'][:8]}" if e.get("commit_sha") else ""
        lines.append(f"• {ts}{branch}{sha}: {e.get('diff_summary', '(no summary)')}")
    return "\n".join(lines)


async def _handle_get_file_symbols(params: dict) -> str:
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id, _get_git_branch
    from agora_code.indexer import index_file
    from pathlib import Path

    file_path = params.get("file_path", "").strip()
    if not file_path:
        return "Error: file_path is required."

    store = get_store()
    pid = _get_project_id()
    branch = _get_git_branch()

    # Try DB first
    syms = store.get_symbols_for_file(file_path, project_id=pid, branch=branch)

    # If not indexed yet and file exists, index it now
    if not syms and Path(file_path).exists():
        count = index_file(file_path, project_id=pid, branch=branch)
        if count:
            syms = store.get_symbols_for_file(file_path, project_id=pid, branch=branch)

    if not syms:
        return (
            f"No symbol index for '{file_path}'. "
            "File will be indexed automatically next time it is read or edited."
        )

    lines = [f"Symbols in {file_path} ({len(syms)} total):"]
    for s in syms:
        note_str = f"  — {s['note']}" if s.get("note") else ""
        end_str = f"-{s['end_line']}" if s.get("end_line") else ""
        lines.append(
            f"  [{s['symbol_type']:8}] {s['symbol_name']:30} "
            f"line {s['start_line']}{end_str}{note_str}"
        )
    lines.append(
        f"\nTip: use Read with offset={syms[0]['start_line']} limit=<end-start> "
        "to read only the function you need."
    )
    return "\n".join(lines)


async def _handle_search_symbols(params: dict) -> str:
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id, _get_git_branch

    query = params.get("query", "").strip()
    if not query:
        return "Error: query is required."

    limit = int(params.get("limit", 10))
    symbol_type = params.get("symbol_type")
    store = get_store()
    pid = _get_project_id()
    branch = _get_git_branch()

    results = store.search_symbol_notes(
        query, k=limit,
        project_id=pid,
        branch=branch,
        symbol_type=symbol_type,
    )

    if not results:
        return f"No symbols matching '{query}' found in the index."

    lines = [f"Symbols matching '{query}' ({len(results)} results):"]
    for r in results:
        note_str = f"  — {r['note']}" if r.get("note") else ""
        end_str = f"-{r['end_line']}" if r.get("end_line") else ""
        lines.append(
            f"  {r['file_path']}:{r['start_line']}{end_str}  "
            f"[{r['symbol_type']}] {r['symbol_name']}{note_str}"
        )
    return "\n".join(lines)


_HANDLERS = {
    "get_session_context":   _handle_get_session_context,
    "save_checkpoint":       _handle_save_checkpoint,
    "store_learning":        _handle_store_learning,
    "recall_learnings":      _handle_recall_learnings,
    "complete_session":      _handle_complete_session,
    "get_memory_stats":      _handle_get_memory_stats,
    "list_sessions":         _handle_list_sessions,
    "store_team_learning":   _handle_store_team_learning,
    "recall_team":           _handle_recall_team,
    "recall_file_history":   _handle_recall_file_history,
    "get_file_symbols":      _handle_get_file_symbols,
    "search_symbols":        _handle_search_symbols,
}


# --------------------------------------------------------------------------- #
#  MCP server loop                                                             #
# --------------------------------------------------------------------------- #

def _send(obj: dict) -> None:
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _dispatch(req: dict) -> Optional[dict]:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "agora-memory", "version": "0.2.3"},
                "capabilities": {"tools": {}},
            }
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args  = params.get("arguments", {})
        handler = _HANDLERS.get(tool_name)
        if not handler:
            return _error(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result = await handler(tool_args)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
        except Exception as e:
            return _error(req_id, -32000, str(e))

    if method in ("ping", "notifications/initialized"):
        if req_id is not None:
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        return None  # notification, no response

    if req_id is not None:
        return _error(req_id, -32601, f"Method not found: {method}")
    return None


async def serve_memory() -> None:
    """Main loop — reads JSON-RPC from stdin, writes to stdout."""
    # Emit session banner on startup
    try:
        from agora_code.session import load_session_if_recent, _get_git_branch
        from agora_code.compress import session_restored_banner
        session = load_session_if_recent(max_age_hours=48)
        if session:
            banner = session_restored_banner(session, token_budget=2000)
            # Branch-change warning
            stored_branch = session.get("branch")
            current_branch = _get_git_branch()
            if stored_branch and current_branch and stored_branch != current_branch:
                banner = (
                    f"⚠️  Branch changed: was `{stored_branch}`, now `{current_branch}`\n\n"
                    + banner
                )
            _send({
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "info", "logger": "agora-memory", "data": banner}
            })
    except Exception:
        pass

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    proto  = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: proto, sys.stdin)

    while True:
        try:
            line = await reader.readline()
            if not line:
                break
            req = json.loads(line.decode())
            resp = await _dispatch(req)
            if resp is not None:
                _send(resp)
        except json.JSONDecodeError:
            continue
        except Exception:
            break
