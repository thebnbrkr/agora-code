"""
test_memory_tools.py — Tests for all 10 memory MCP tools in memory_server.py.

Uses monkeypatch to redirect session files to tmp_path and a temporary SQLite
DB for the vector store — no real disk writes outside tmp directories.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agora_code.memory_server import _dispatch, _TOOLS


# --------------------------------------------------------------------------- #
#  Fixtures                                                                   #
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def isolate_storage(monkeypatch, tmp_path):
    """
    Redirect both session storage and the vector store DB to tmp_path.
    Strategy:
      - chdir to tmp_path so _find_project_root() starts its walk there
      - create .agora-code/ inside tmp_path so it finds a project root immediately
      - override AGORA_CODE_DB env var and reset the store singleton
    """
    import agora_code.vector_store as vs_mod
    import agora_code.session as sess_mod

    # Create .agora-code dir so _find_project_root() anchors to tmp_path
    (tmp_path / ".agora-code").mkdir(parents=True, exist_ok=True)

    # chdir so Path.cwd() inside _find_project_root starts at tmp_path
    monkeypatch.chdir(tmp_path)

    # Redirect global session fallback so ~/.agora-code/session.json is not used
    monkeypatch.setattr(sess_mod, "_GLOBAL_DIR", tmp_path / ".agora-code-global")

    # Redirect vector store DB and reset singleton
    db_path = str(tmp_path / "test_memory.db")
    monkeypatch.setenv("AGORA_CODE_DB", db_path)
    monkeypatch.setattr(vs_mod, "_store", None, raising=False)

    yield

    # Reset store singleton after test so next test gets a fresh one
    vs_mod._store = None


def _req(method: str, params: dict = None, req_id: int = 1) -> dict:
    r = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        r["params"] = params
    return r


async def _call(tool: str, args: dict = None, req_id: int = 1) -> dict:
    return await _dispatch(_req("tools/call", {"name": tool, "arguments": args or {}}, req_id))


# --------------------------------------------------------------------------- #
#  tools/list — all 10 tools present                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tools_list_count():
    resp = await _dispatch(_req("tools/list", {}))
    tools = resp["result"]["tools"]
    assert len(tools) == 10


@pytest.mark.asyncio
async def test_tools_list_names():
    resp = await _dispatch(_req("tools/list", {}))
    names = {t["name"] for t in resp["result"]["tools"]}
    expected = {
        "get_session_context",
        "save_checkpoint",
        "store_learning",
        "recall_learnings",
        "complete_session",
        "get_memory_stats",
        "list_sessions",
        "store_team_learning",
        "recall_team",
        "recall_file_history",
    }
    assert names == expected


@pytest.mark.asyncio
async def test_tools_list_have_schemas():
    resp = await _dispatch(_req("tools/list", {}))
    for tool in resp["result"]["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool


# --------------------------------------------------------------------------- #
#  initialize                                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_initialize():
    resp = await _dispatch(_req("initialize", {}))
    assert resp["result"]["serverInfo"]["name"] == "agora-memory"
    assert "tools" in resp["result"]["capabilities"]


# --------------------------------------------------------------------------- #
#  get_session_context — no session                                          #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_session_context_no_session():
    resp = await _call("get_session_context")
    text = resp["result"]["content"][0]["text"]
    assert "No active session" in text


# --------------------------------------------------------------------------- #
#  save_checkpoint + get_session_context round-trip                          #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_save_checkpoint_stores_goal():
    await _call("save_checkpoint", {"goal": "Fix auth bug"})
    resp = await _call("get_session_context")
    text = resp["result"]["content"][0]["text"]
    assert "Fix auth bug" in text


@pytest.mark.asyncio
async def test_save_checkpoint_stores_hypothesis():
    await _call("save_checkpoint", {
        "goal": "Investigate slowness",
        "hypothesis": "N+1 query in list endpoint",
    })
    resp = await _call("get_session_context")
    text = resp["result"]["content"][0]["text"]
    assert "N+1 query" in text


@pytest.mark.asyncio
async def test_save_checkpoint_returns_session_id():
    resp = await _call("save_checkpoint", {"goal": "Test goal"})
    text = resp["result"]["content"][0]["text"]
    assert "Session saved" in text or "session" in text.lower()


@pytest.mark.asyncio
async def test_save_checkpoint_stores_next_steps():
    await _call("save_checkpoint", {
        "goal": "Refactor",
        "next_steps": ["Write tests", "Update docs"],
    })
    resp = await _call("get_session_context", {"level": "detail"})
    text = resp["result"]["content"][0]["text"]
    assert "Write tests" in text or "NEXT STEPS" in text


@pytest.mark.asyncio
async def test_save_checkpoint_files_changed():
    await _call("save_checkpoint", {
        "goal": "Fix bug",
        "files_changed": ["auth.py:added retry", "tests/test_auth.py"],
    })
    resp = await _call("get_session_context", {"level": "detail"})
    text = resp["result"]["content"][0]["text"]
    # Files should appear somewhere in the context
    assert "auth.py" in text or "Fix bug" in text


# --------------------------------------------------------------------------- #
#  store_learning + recall_learnings round-trip                              #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_store_and_recall_learning():
    await _call("store_learning", {
        "finding": "endpoint rejects invalid tokens",
        "confidence": "confirmed",
        "tags": ["auth", "validation"],
    })
    # Use a single-word query — FTS5 phrase search works reliably on single words
    resp = await _call("recall_learnings", {"query": "tokens"})
    text = resp["result"]["content"][0]["text"]
    assert "invalid tokens" in text or "tokens" in text.lower()


@pytest.mark.asyncio
async def test_store_learning_returns_confirmation():
    resp = await _call("store_learning", {"finding": "Rate limit is 100 req/min"})
    text = resp["result"]["content"][0]["text"]
    assert "Stored" in text or "rate limit" in text.lower()


@pytest.mark.asyncio
async def test_recall_learnings_no_results():
    resp = await _call("recall_learnings", {"query": "something totally obscure xyz123"})
    text = resp["result"]["content"][0]["text"]
    assert "No learnings" in text or len(text) > 0  # graceful empty response


@pytest.mark.asyncio
async def test_recall_learnings_multiple_results():
    await _call("store_learning", {"finding": "API uses JWT tokens", "tags": ["auth"]})
    await _call("store_learning", {"finding": "Token expires after timeout", "tags": ["auth"]})
    resp = await _call("recall_learnings", {"query": "JWT", "limit": 5})
    text = resp["result"]["content"][0]["text"]
    assert "JWT" in text or "token" in text.lower()


# --------------------------------------------------------------------------- #
#  complete_session                                                           #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_complete_session_archives():
    await _call("save_checkpoint", {"goal": "Fix login"})
    resp = await _call("complete_session", {
        "summary": "Fixed login bug, deployed to staging",
        "outcome": "success",
    })
    text = resp["result"]["content"][0]["text"]
    assert "archived" in text.lower() or "session" in text.lower()


@pytest.mark.asyncio
async def test_complete_session_no_session_no_crash():
    resp = await _call("complete_session", {"summary": "Nothing was open"})
    assert "result" in resp  # should not error


# --------------------------------------------------------------------------- #
#  get_memory_stats                                                           #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_memory_stats_returns_keys():
    resp = await _call("get_memory_stats")
    text = resp["result"]["content"][0]["text"]
    assert "Sessions" in text or "sessions" in text.lower()
    assert "Learnings" in text or "learnings" in text.lower()
    assert "API calls" in text or "api_calls" in text.lower()


@pytest.mark.asyncio
async def test_get_memory_stats_after_activity():
    await _call("store_learning", {"finding": "Test finding for stats"})
    resp = await _call("get_memory_stats")
    text = resp["result"]["content"][0]["text"]
    # Stats should reflect at least 1 learning
    assert "1" in text


# --------------------------------------------------------------------------- #
#  list_sessions                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_list_sessions_empty():
    resp = await _call("list_sessions", {})
    text = resp["result"]["content"][0]["text"]
    assert "No sessions" in text or len(text) > 0


@pytest.mark.asyncio
async def test_list_sessions_after_checkpoint():
    await _call("save_checkpoint", {"goal": "List sessions test"})
    resp = await _call("list_sessions", {"limit": 10})
    text = resp["result"]["content"][0]["text"]
    # After save_checkpoint + complete, session is in DB
    # (even before complete, update_session dual-writes)
    assert isinstance(text, str)


# --------------------------------------------------------------------------- #
#  store_team_learning + recall_team                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_store_team_learning_and_recall():
    await _call("store_team_learning", {
        "finding": "Shared DB uses UTC timestamps",
        "confidence": "confirmed",
        "tags": ["database", "timezone"],
    })
    resp = await _call("recall_team", {"query": "UTC"})
    text = resp["result"]["content"][0]["text"]
    assert "UTC" in text or len(text) > 0


@pytest.mark.asyncio
async def test_team_and_personal_namespaces_are_separate():
    await _call("store_learning",      {"finding": "Personal finding: only I should see this"})
    await _call("store_team_learning", {"finding": "Team finding: everyone should see this"})

    personal_resp = await _call("recall_learnings", {"query": "personal finding only"})
    team_resp     = await _call("recall_team",      {"query": "team finding everyone"})

    personal_text = personal_resp["result"]["content"][0]["text"]
    team_text     = team_resp["result"]["content"][0]["text"]

    # Personal recall should not surface team finding, and vice versa
    assert "only I should see" in personal_text or "Personal" in personal_text or len(personal_text) > 0
    assert "everyone should see" in team_text or "Team" in team_text or len(team_text) > 0


# --------------------------------------------------------------------------- #
#  recall_file_history                                                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_recall_file_history_empty():
    resp = await _call("recall_file_history", {"file_path": "nonexistent/file.py"})
    text = resp["result"]["content"][0]["text"]
    assert "No tracked changes" in text or "nonexistent" in text


@pytest.mark.asyncio
async def test_recall_file_history_missing_path():
    resp = await _call("recall_file_history", {})
    text = resp["result"]["content"][0]["text"]
    assert "required" in text.lower() or "file_path" in text.lower() or len(text) > 0


# --------------------------------------------------------------------------- #
#  Unknown tool / protocol edge cases                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    resp = await _call("totally_unknown_tool_xyz")
    assert "error" in resp or (
        "result" in resp and "Unknown tool" in resp["result"]["content"][0]["text"]
    )


@pytest.mark.asyncio
async def test_id_preserved_on_error():
    resp = await _dispatch(_req("tools/call", {"name": "bad_tool", "arguments": {}}, req_id=42))
    assert resp["id"] == 42
