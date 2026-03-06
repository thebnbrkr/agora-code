"""
test_memory.py — AgentMemory + APICallNode memory integration tests.

Skipped automatically if agora-mem is not installed.
Uses in-memory SQLite (db_path=":memory:") — no files written.
"""
from __future__ import annotations

import pytest

from agora_code.agent import APICallNode, _merge_stats
from agora_code.models import Param, Route


# --------------------------------------------------------------------------- #
#  _merge_stats unit tests (pure, no I/O)                                     #
# --------------------------------------------------------------------------- #

def test_merge_stats_first_call():
    stats = _merge_stats({}, {"status": 200, "_latency_ms": 100.0})
    assert stats["total_calls"] == 1
    assert stats["error_count"] == 0
    assert stats["success_rate"] == 1.0
    assert stats["avg_latency_ms"] == 100.0
    assert stats["last_error"] is None


def test_merge_stats_error_call():
    stats = _merge_stats({}, {"status": 500, "_error": "Internal Server Error"})
    assert stats["error_count"] == 1
    assert stats["success_rate"] == 0.0
    assert stats["last_error"] == "Internal Server Error"


def test_merge_stats_accumulates():
    prev = {
        "total_calls": 5,
        "error_count": 1,
        "latencies": [100.0, 150.0, 200.0, 120.0, 180.0],
    }
    stats = _merge_stats(prev, {"status": 200, "_latency_ms": 90.0})
    assert stats["total_calls"] == 6
    assert stats["error_count"] == 1
    assert stats["success_rate"] == pytest.approx(5 / 6, rel=0.01)
    assert len(stats["latencies"]) == 6
    assert stats["latencies"][-1] == 90.0


def test_merge_stats_rolling_window():
    """Latency window capped at 50 samples."""
    prev = {"total_calls": 50, "error_count": 0, "latencies": [100.0] * 50}
    stats = _merge_stats(prev, {"status": 200, "_latency_ms": 999.0})
    assert len(stats["latencies"]) == 50
    assert stats["latencies"][-1] == 999.0
    assert stats["latencies"][0] == 100.0  # oldest rolled off


def test_merge_stats_no_latency():
    """Latency is optional — no _latency_ms key."""
    stats = _merge_stats({}, {"status": 200})
    assert stats["avg_latency_ms"] is None
    assert stats["latencies"] == []


# --------------------------------------------------------------------------- #
#  APICallNode with memory                                                     #
# --------------------------------------------------------------------------- #

@pytest.fixture
def user_route():
    return Route(
        method="GET",
        path="/users/{user_id}",
        params=[Param(name="user_id", type="int", location="path", required=True)],
    )


@pytest.mark.asyncio
async def test_first_call_no_context(user_route, memory_store):
    """First call has no memory context — context_lines is empty."""
    node = APICallNode(
        route=user_route,
        base_url="http://localhost:8000",
        memory_store=memory_store,
    )
    node._http_call = _mock_ok  # patch HTTP

    result, context = await node.run({"user_id": 1})
    assert result["status"] == 200
    assert context == []  # no prior stats yet


@pytest.mark.asyncio
async def test_second_call_shows_context(user_route, memory_store):
    """After the first call is stored, the second call surfaces stats."""
    node = APICallNode(
        route=user_route,
        base_url="http://localhost:8000",
        memory_store=memory_store,
    )
    node._http_call = _mock_ok

    await node.run({"user_id": 1})          # first call — stores stats
    _, context = await node.run({"user_id": 2})  # second call — reads them

    assert any("📊" in line for line in context)
    assert any("100% success" in line or "1 past" in line.lower() for line in context)


@pytest.mark.asyncio
async def test_error_surfaces_in_context(user_route, memory_store):
    """After a failed call, the next call shows the last error."""
    node = APICallNode(
        route=user_route,
        base_url="http://localhost:8000",
        memory_store=memory_store,
    )
    node._http_call = _mock_error

    await node.run({"user_id": 999})

    node._http_call = _mock_ok
    _, context = await node.run({"user_id": 1})

    assert any("⚠️" in line for line in context)
    assert any("Not found" in line for line in context)


@pytest.mark.asyncio
async def test_stats_persisted_across_nodes(user_route, memory_store):
    """Stats persist in MemoryStore across separate node instances."""
    n1 = APICallNode(route=user_route, base_url="http://localhost", memory_store=memory_store)
    n1._http_call = _mock_ok
    await n1.run({"user_id": 1})

    n2 = APICallNode(route=user_route, base_url="http://localhost", memory_store=memory_store)
    n2._http_call = _mock_ok
    _, context = await n2.run({"user_id": 2})

    assert any("📊" in line for line in context)


@pytest.mark.asyncio
async def test_node_works_without_memory(user_route):
    """memory_store=None — no crash, no context."""
    node = APICallNode(
        route=user_route,
        base_url="http://localhost:8000",
        memory_store=None,
    )
    node._http_call = _mock_ok
    result, context = await node.run({"user_id": 1})
    assert result["status"] == 200
    assert context == []


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

async def _mock_ok(args):
    return {"body": {"id": 1, "name": "Alice"}, "status": 200, "_latency_ms": 50.0}


async def _mock_error(args):
    return {"body": {}, "status": 404, "_error": "Not found", "_latency_ms": 20.0}



def test_merge_stats_zero_latency():
    """✅ NEW TEST: Zero latency should be recorded (not treated as falsy)."""
    from agora_code.agent import _merge_stats
    
    stats = _merge_stats({}, {"status": 200, "_latency_ms": 0.0})
    
    assert stats["total_calls"] == 1
    assert stats["latencies"] == [0.0]
    assert stats["avg_latency_ms"] == 0.0


def test_merge_stats_none_latency():
    """✅ NEW TEST: Missing latency should not be added to list."""
    from agora_code.agent import _merge_stats
    
    stats = _merge_stats({}, {"status": 200})  # No _latency_ms
    
    assert stats["latencies"] == []
    assert stats["avg_latency_ms"] is None