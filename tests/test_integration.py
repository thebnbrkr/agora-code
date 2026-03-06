"""
test_integration.py — End-to-end: scan → serve → call tool.

No real API server needed — HTTP is mocked after scan.
Tests the full pipeline: scan code, build tool list, dispatch call.
"""
from __future__ import annotations

import json
import os
import textwrap

import pytest

from agora_code import scan, MCPServer


# --------------------------------------------------------------------------- #
#  Full pipeline: FastAPI code → scan → tool call                             #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_scan_and_serve_fastapi(tmp_path):
    """Write a FastAPI file, scan it, start server, call a tool."""
    (tmp_path / "app.py").write_text(textwrap.dedent("""\
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/health")
        def health():
            \"\"\"Health check.\"\"\"
            pass

        @app.post("/echo")
        async def echo(message: str):
            \"\"\"Echo a message.\"\"\"
            pass
    """))

    # Tier 2: Python AST scan
    catalog = await scan(str(tmp_path))
    assert len(catalog.routes) >= 2
    assert catalog.extractor == "ast"

    # Build MCP server (no memory, no auth)
    server = MCPServer(catalog, base_url="http://localhost:8000")

    # Patch HTTP so we don't need a live server
    async def mock_http(args):
        return {"body": {"status": "ok"}, "status": 200, "_latency_ms": 5.0}
    for node in server._nodes.values():
        node._http_call = mock_http

    # List tools
    list_resp = await server._dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    tool_names = {t["name"] for t in list_resp["result"]["tools"]}
    assert "get_health" in tool_names or "get_health_" in str(tool_names)

    # Call the health tool
    call_resp = await server._dispatch({
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": "get_health", "arguments": {}},
    })
    assert "result" in call_resp
    text = call_resp["result"]["content"][0]["text"]
    assert "✅" in text
    assert "200" in text


# --------------------------------------------------------------------------- #
#  OpenAPI scan → serve                                                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_scan_openapi_and_list_tools(tmp_path):
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/ping": {"get": {"summary": "Ping", "parameters": []}},
            "/users": {"post": {"summary": "Create user", "parameters": []}},
        }
    }
    (tmp_path / "openapi.json").write_text(json.dumps(spec))

    catalog = await scan(str(tmp_path))
    assert catalog.extractor == "openapi"
    assert len(catalog.routes) == 2

    server = MCPServer(catalog, base_url="http://localhost:9000")
    list_resp = await server._dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    names = {t["name"] for t in list_resp["result"]["tools"]}
    assert len(names) == 2


# --------------------------------------------------------------------------- #
#  RouteCatalog helpers                                                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_catalog_to_mcp_tools(sample_catalog):
    tools = sample_catalog.to_mcp_tools()
    assert len(tools) == 2
    for t in tools:
        assert "name" in t
        assert "inputSchema" in t
        props = t["inputSchema"]["properties"]
        assert isinstance(props, dict)


@pytest.mark.asyncio
async def test_catalog_to_openapi(sample_catalog):
    spec = sample_catalog.to_openapi()
    assert "paths" in spec
    assert "openapi" in spec


# --------------------------------------------------------------------------- #
#  Regex fallback scan                                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_regex_fallback_on_js(tmp_path):
    """Regex tier fires for JS files (not Python, no OpenAPI spec)."""
    (tmp_path / "routes.js").write_text(textwrap.dedent("""\
        app.get('/api/users', (req, res) => res.json([]));
        app.post('/api/users', (req, res) => res.json({}));
    """))

    catalog = await scan(str(tmp_path))
    # Regex or LLM tier — at least regex should run
    assert catalog.extractor in ("regex", "llm")
    assert len(catalog.routes) >= 1
