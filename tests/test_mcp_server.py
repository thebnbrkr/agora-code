"""
test_mcp_server.py — JSON-RPC 2.0 / MCP protocol tests.

Tests the MCPServer dispatcher directly (no stdio I/O).
Mocks HTTP calls so no real API server is needed.
"""
from __future__ import annotations

import json

import pytest

from agora_code.agent import MCPServer, _err, _ok
from agora_code.models import Param, Route, RouteCatalog


# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #

@pytest.fixture
def two_route_catalog():
    return RouteCatalog(
        source="test",
        extractor="test",
        routes=[
            Route(method="GET", path="/users", description="List users", params=[]),
            Route(
                method="POST",
                path="/users",
                description="Create user",
                params=[
                    Param(name="name", type="str", location="body", required=True),
                ],
            ),
        ],
    )


@pytest.fixture
def server(two_route_catalog):
    return MCPServer(two_route_catalog, base_url="http://localhost:8000")


def _req(method: str, params: dict = None, req_id: int = 1) -> dict:
    r = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        r["params"] = params
    return r


# --------------------------------------------------------------------------- #
#  initialize                                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_initialize(server):
    resp = await server._dispatch(_req("initialize", {}))
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "agora-code"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_initialize_id_preserved(server):
    resp = await server._dispatch(_req("initialize", {}, req_id=42))
    assert resp["id"] == 42


# --------------------------------------------------------------------------- #
#  tools/list                                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tools_list_count(server):
    resp = await server._dispatch(_req("tools/list", {}))
    tools = resp["result"]["tools"]
    assert len(tools) == 2


@pytest.mark.asyncio
async def test_tools_list_structure(server):
    resp = await server._dispatch(_req("tools/list", {}))
    tool = resp["result"]["tools"][0]
    assert "name" in tool
    assert "description" in tool
    assert "inputSchema" in tool
    assert tool["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
async def test_tools_list_tool_names(server):
    resp = await server._dispatch(_req("tools/list", {}))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "get_users" in names
    assert "post_users" in names


# --------------------------------------------------------------------------- #
#  tools/call — success                                                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tool_call_success(server):
    _patch_http(server, {"body": {"users": []}, "status": 200, "_latency_ms": 30.0})

    resp = await server._dispatch(_req("tools/call", {
        "name": "get_users",
        "arguments": {},
    }))

    assert "result" in resp
    text = resp["result"]["content"][0]["text"]
    assert "✅" in text
    assert "200" in text


@pytest.mark.asyncio
async def test_tool_call_body_in_response(server):
    body = {"users": [{"id": 1, "name": "Alice"}]}
    _patch_http(server, {"body": body, "status": 200, "_latency_ms": 10.0})

    resp = await server._dispatch(_req("tools/call", {
        "name": "get_users", "arguments": {},
    }))
    text = resp["result"]["content"][0]["text"]
    assert "Alice" in text


# --------------------------------------------------------------------------- #
#  tools/call — errors                                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tool_call_http_error(server):
    _patch_http(server, {"body": {}, "status": 404, "_error": "Not found", "_latency_ms": 5.0})

    resp = await server._dispatch(_req("tools/call", {
        "name": "get_users", "arguments": {},
    }))
    text = resp["result"]["content"][0]["text"]
    assert "❌" in text
    assert "404" in text
    assert "Not found" in text


@pytest.mark.asyncio
async def test_tool_call_unknown_tool(server):
    resp = await server._dispatch(_req("tools/call", {
        "name": "totally_unknown_tool",
        "arguments": {},
    }))
    text = resp["result"]["content"][0]["text"]
    assert "Unknown tool" in text


# --------------------------------------------------------------------------- #
#  Protocol edge cases                                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_notification_returns_none(server):
    """Notifications (no id) must return None — no response sent."""
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = await server._dispatch(notif)
    assert resp is None


@pytest.mark.asyncio
async def test_ping(server):
    resp = await server._dispatch(_req("ping", {}))
    assert resp["result"] == {}


@pytest.mark.asyncio
async def test_method_not_found(server):
    resp = await server._dispatch(_req("unknown/method"))
    assert "error" in resp
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_error_id_matches_request(server):
    resp = await server._dispatch(_req("unknown/method", req_id=99))
    assert resp["id"] == 99


# --------------------------------------------------------------------------- #
#  Helper: patch HTTP on all nodes                                             #
# --------------------------------------------------------------------------- #

def _patch_http(server: MCPServer, return_value: dict) -> None:
    async def _mock(args):
        return return_value
    for node in server._nodes.values():
        node._http_call = _mock
