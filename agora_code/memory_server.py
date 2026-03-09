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
                    "default": "summary",
                    "description": "How much detail to include. summary (~200 tokens) is usually enough."
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
    }
]


# --------------------------------------------------------------------------- #
#  Tool handlers                                                               #
# --------------------------------------------------------------------------- #

async def _handle_get_session_context(params: dict) -> str:
    from agora_code.session import load_session
    from agora_code.tldr import compress_session, session_restored_banner

    session = load_session()
    if not session:
        return "No active session. Start one with save_checkpoint(goal='...')."

    level = params.get("level", "summary")
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


async def _handle_store_learning(params: dict) -> str:
    from agora_code.vector_store import get_store
    from agora_code.embeddings import get_embedding

    finding = params.get("finding", "")
    embedding = get_embedding(finding)
    store = get_store()
    lid = store.store_learning(
        finding,
        evidence=params.get("evidence"),
        confidence=params.get("confidence", "confirmed"),
        tags=params.get("tags", []),
        embedding=embedding,
    )
    return f"Stored learning: {finding[:80]}{'...' if len(finding) > 80 else ''}"


async def _handle_recall_learnings(params: dict) -> str:
    from agora_code.vector_store import get_store
    from agora_code.embeddings import get_embedding, is_available

    query = params.get("query", "")
    limit = int(params.get("limit", 5))
    store = get_store()

    if is_available():
        emb = get_embedding(query)
        if emb:
            results = store.search_learnings_semantic(emb, limit=limit)
        else:
            results = store.search_learnings_keyword(query, limit=limit)
    else:
        results = store.search_learnings_keyword(query, limit=limit)

    if not results:
        return f"No learnings found for '{query}'. Store one with store_learning()."

    lines = [f"Found {len(results)} learning(s) for '{query}':\n"]
    for i, r in enumerate(results, 1):
        conf = {"confirmed": "✓", "likely": "~", "hypothesis": "?"}.get(r.get("confidence", ""), "")
        lines.append(f"{i}. {conf} {r['finding']}")
        if r.get("evidence"):
            lines.append(f"   Evidence: {r['evidence']}")
        if r.get("tags"):
            lines.append(f"   Tags: {', '.join(r['tags'])}")
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


_HANDLERS = {
    "get_session_context": _handle_get_session_context,
    "save_checkpoint":     _handle_save_checkpoint,
    "store_learning":      _handle_store_learning,
    "recall_learnings":    _handle_recall_learnings,
    "complete_session":    _handle_complete_session,
    "get_memory_stats":    _handle_get_memory_stats,
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
                "serverInfo": {"name": "agora-memory", "version": "0.1.0"},
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
        from agora_code.session import load_session_if_recent
        from agora_code.tldr import session_restored_banner
        session = load_session_if_recent(max_age_hours=48)
        if session:
            banner = session_restored_banner(session, token_budget=2000)
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
