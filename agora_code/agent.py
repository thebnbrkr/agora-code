"""
agent.py — MCP server with optional memory backend (MemoryNode lifecycle).

Each API route becomes an APICallNode:
  prep_async   → load historical stats for this route (when memory backend available)
  exec_async   → execute the real HTTP request
  post_async   → merge + save updated stats (when memory backend available)

MCPServer exposes all nodes as MCP tools over stdio.
Compatible with Claude Desktop, Cline, and any JSON-RPC 2.0 MCP client.

Usage:
    from agora_code import scan
    from agora_code.agent import MCPServer

    catalog = await scan("./my-api")
    server = MCPServer(catalog, base_url="http://localhost:8000")
    await server.serve()

Claude Desktop / Cline config:
    {
      "mcpServers": {
        "my-api": {
          "command": "agora-code",
          "args": ["serve", "./my-api", "--url", "http://localhost:8000"]
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from agora_code.models import Route, RouteCatalog

# Optional memory backend — import at class definition time so we can
# pick the right base class when available.
try:
    from agora_mem.node import MemoryNode as _MemoryNodeBase
    from agora_mem.store import MemoryStore
    _HAS_MEMORY = True
except ImportError:
    _HAS_MEMORY = False
    _MemoryNodeBase = object   # plain object fallback


# --------------------------------------------------------------------------- #
#  APICallNode — MemoryNode subclass (or plain class if memory backend missing) #
# --------------------------------------------------------------------------- #

class APICallNode(_MemoryNodeBase):  # type: ignore[misc]
    """
    Wraps one Route in prep→exec→post lifecycle (with optional memory backend).

    Session key pattern:  "route:{METHOD}:{path}"
    Stats are kept separate from raw HTTP results — post_async saves
    accumulated stats (call count, success rate, latency p50, last error)
    rather than the raw HTTP body, so the session is always meaningful.

    Falls back to a no-memory mode if the optional memory backend is not installed.

    Args:
        route:         the Route this node handles
        base_url:      e.g. "http://localhost:8000"
        memory_store:  optional memory store instance, or None
        auth:          {"type": "bearer"|"api-key"|"basic", "token": "..."}
    """

    def __init__(
        self,
        route: Route,
        base_url: str,
        memory_store=None,
        auth: Optional[Dict] = None,
    ):
        self.route = route
        self.base_url = base_url.rstrip("/")
        self.auth = auth or {}

        # Unique session ID per route — stores accumulated stats
        self._route_session_id = f"route:{route.method}:{route.path}"

        if _HAS_MEMORY and memory_store is not None:
            super().__init__(
                memory=memory_store,
                session_key="_route_session_id",
                ttl_seconds=None,      # never expires — stats are cumulative
                auto_compress=False,
            )
            self._use_memory = True
        else:
            self._use_memory = False

    # ------------------------------------------------------------------ #
    #  MemoryNode lifecycle                                                #
    # ------------------------------------------------------------------ #

    async def exec_async(self, prep_res: Dict) -> Dict:
        """Execute the HTTP call. prep_res already has _memory_state from prep."""
        args = prep_res.get("_call_args", {})
        return await self._http_call(args)

    async def post_async(self, shared: Dict, prep_res: Dict, exec_res: Dict) -> Dict:
        """
        Save merged stats (not raw HTTP body) as the session state.
        This keeps the session record meaningful across all calls to this route.
        Returns exec_res unchanged so MCPServer gets the HTTP result.
        """
        if not self._use_memory:
            return exec_res

        prev_stats = prep_res.get("_memory_state") or {}
        updated_stats = _merge_stats(prev_stats, exec_res)

        # Store stats under the route session ID
        await self.memory.store(self._route_session_id, updated_stats)
        return exec_res

    # ------------------------------------------------------------------ #
    #  Public run method                                                   #
    # ------------------------------------------------------------------ #

    async def run(self, args: Dict) -> Tuple[Dict, List[str]]:
        """
        Execute this route, returning (http_result, context_lines).

        context_lines: memory context formatted for Claude to read.
        """
        if self._use_memory:
            shared = {
                "_route_session_id": self._route_session_id,
                "_call_args": args,
            }
            # Full prep → exec → post via MemoryNode.run_async
            http_result = await self.run_async(shared)
            context_lines = _format_context(shared.get("_memory_state") or {})
        else:
            http_result = await self._http_call(args)
            context_lines = []

        return http_result, context_lines

    # ------------------------------------------------------------------ #
    #  HTTP execution                                                      #
    # ------------------------------------------------------------------ #

    async def _http_call(self, args: Dict) -> Dict:
        """Run HTTP call in a thread pool (stdlib urllib, zero extra deps)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._http_call_sync, args)

    def _http_call_sync(self, args: Dict) -> Dict:
        path = self.route.path
        query_params: Dict[str, Any] = {}
        body_params: Dict[str, Any] = {}

        param_locations = {p.name: p.location for p in self.route.params}
        for k, v in args.items():
            loc = param_locations.get(k, "query")
            if loc == "path":
                path = path.replace(f"{{{k}}}", str(v))
            elif loc == "body":
                body_params[k] = v
            else:
                query_params[k] = v

        url = self.base_url + path
        if query_params:
            url += "?" + urllib.parse.urlencode(query_params)

        data = json.dumps(body_params).encode() if body_params else None
        req = urllib.request.Request(url, data=data, method=self.route.method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        self._inject_auth(req)

        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                body = json.loads(resp.read().decode("utf-8"))
                return {
                    "body": body,
                    "status": resp.status,
                    "_latency_ms": (time.monotonic() - t0) * 1000,
                }
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode())
                msg = (err_body.get("detail")
                       or err_body.get("message")
                       or str(e))
            except Exception:
                msg = str(e)
            return {
                "body": {},
                "status": e.code,
                "_error": msg,
                "_latency_ms": (time.monotonic() - t0) * 1000,
            }
        except Exception as e:
            return {"body": {}, "status": 0, "_error": str(e)}

    def _inject_auth(self, req: urllib.request.Request) -> None:
        kind = self.auth.get("type", "").lower()
        if kind == "bearer":
            token = self.auth.get("token", "")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
        elif kind == "api-key":
            token = self.auth.get("token", "")
            if token:
                header = self.auth.get("header", "X-API-Key")
                req.add_header(header, token)
        elif kind == "basic":
            import base64
            username = self.auth.get("username", "")
            password = self.auth.get("password", "")
            if username and password:
                creds = base64.b64encode(
                    f"{username}:{password}".encode()
                ).decode()
                req.add_header("Authorization", f"Basic {creds}")


# --------------------------------------------------------------------------- #
#  MCPServer — Claude Desktop / Cline compatible stdio server                 #
# --------------------------------------------------------------------------- #

class MCPServer:
    """
    MCP stdio server. One APICallNode per route.
    Handles JSON-RPC 2.0 over newline-delimited stdin/stdout.
    """

    def __init__(
        self,
        catalog: RouteCatalog,
        base_url: str,
        memory_store=None,
        auth: Optional[Dict] = None,
        edition: str = "community",
    ):
        self.catalog = catalog
        self.edition = edition
        self._nodes: Dict[str, APICallNode] = {
            route.tool_name: APICallNode(
                route=route,
                base_url=base_url,
                memory_store=memory_store,
                auth=auth,
            )
            for route in catalog.routes
        }

        # ── Session restoration (context manager layer) ──────────────────
        # Load JSON session if one exists from the last 24h.
        # The compressed banner is emitted once at startup so the AI assistant
        # immediately knows where the user left off — no prompt needed.
        self._restored_banner: Optional[str] = None
        try:
            from agora_code.session import load_session_if_recent
            from agora_code.compress import session_restored_banner
            session = load_session_if_recent(max_age_hours=24)
            if session:
                self._restored_banner = session_restored_banner(session)
        except Exception:
            pass  # non-fatal — memory is additive

        # ── VectorStore for call logging ──────────────────────────────────
        self._vs = None
        self._session_id: Optional[str] = None
        try:
            from agora_code.vector_store import get_store
            from agora_code.session import load_session
            self._vs = get_store()
            sess = load_session()
            if sess:
                self._session_id = sess.get("session_id")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Stdio server loop                                                   #
    # ------------------------------------------------------------------ #

    async def serve(self) -> None:
        """Run the MCP stdio server until stdin closes."""
        _log(f"agora-code MCP ({self.edition}) — {len(self._nodes)} tools")
        _log(f"source: {self.catalog.source}")

        stdin = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(stdin)
        loop = asyncio.get_event_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        # Emit session-restored banner as a log notification so the AI sees it
        # immediately without any user prompt needed.
        if self._restored_banner:
            notif = {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": "info",
                    "logger": "agora-code",
                    "data": self._restored_banner,
                },
            }
            sys.stdout.write(json.dumps(notif) + "\n")
            sys.stdout.flush()

        while True:
            request: Optional[Dict] = None
            raw_line: str = ""
            try:
                raw = await asyncio.wait_for(stdin.readline(), timeout=600)
                if not raw:  # EOF — client disconnected
                    _log("stdin closed, shutting down")
                    break

                raw_line = raw.decode("utf-8").strip()
                if not raw_line:
                    continue

                request = json.loads(raw_line)
                response = await self._dispatch(request)

                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()

            except asyncio.TimeoutError:
                _log("No activity for 10 minutes, shutting down")
                break

            except json.JSONDecodeError:
                # JSON-RPC spec: respond with parse error when id is unknown
                err = _err(None, -32700, "Parse error")
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()

            except Exception as e:
                _log(f"Server error: {e}")
                # Best-effort: send internal error if we have a request id
                try:
                    req_id = request.get("id") if request else None
                    if req_id is not None:
                        resp = _err(req_id, -32603, f"Internal error: {e}")
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    #  JSON-RPC dispatcher                                                 #
    # ------------------------------------------------------------------ #

    async def _dispatch(self, req: Dict) -> Optional[Dict]:
        method = req.get("method", "")
        req_id = req.get("id")

        # Notifications: no id, no response
        if "id" not in req:
            return None

        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "agora-code",
                    "version": "0.2.1",
                    "edition": self.edition,
                },
            })

        if method == "tools/list":
            return _ok(req_id, {"tools": self.catalog.to_mcp_tools()})

        if method == "tools/call":
            params = req.get("params", {})
            result = await self._handle_tool_call(
                params.get("name", ""),
                params.get("arguments", {}),
            )
            return _ok(req_id, result)

        if method == "ping":
            return _ok(req_id, {})

        return _err(req_id, -32601, f"Method not found: {method}")

    # ------------------------------------------------------------------ #
    #  Tool call handler                                                   #
    # ------------------------------------------------------------------ #

    async def _handle_tool_call(self, tool_name: str, args: Dict) -> Dict:
        node = self._nodes.get(tool_name)
        if not node:
            available = list(self._nodes)[:5]
            return _text(
                f"Unknown tool: {tool_name!r}\n"
                f"Available tools: {available}"
            )

        result, context_lines = await node.run(args)

        status = result.get("status", 0)
        latency = result.get("_latency_ms", 0.0)
        error = result.get("_error")
        body = result.get("body", {})
        success = not error and 200 <= status < 300

        # ── Log call to VectorStore for pattern detection ─────────────────
        if self._vs is not None:
            try:
                self._vs.log_api_call(
                    session_id=self._session_id,
                    method=node.route.method,
                    path=node.route.path,
                    request_params=args,
                    response_status=status,
                    latency_ms=latency,
                    success=success,
                    error_message=error,
                )
            except Exception:
                pass

        lines: List[str] = []

        # ── Surface failure patterns (token-efficient hint) ───────────────
        if not success and self._vs is not None:
            try:
                patterns = self._vs.get_failure_patterns(node.route.path, min_occurrences=3)
                for p in patterns[:1]:  # show max 1 hint to save tokens
                    lines.append(
                        f"💡 PATTERN: {p['params']} has failed "
                        f"{p['occurrences']}x ({int(p['success_rate']*100)}% success rate). "
                        f"{p['suggestion']}"
                    )
                    lines.append("")
            except Exception:
                pass

        # ── optional memory backend: per-route stats ────────────────────────
        if context_lines:
            lines.extend(context_lines)
            lines.append("")

        if error:
            lines.append(f"❌ {node.route.method} {node.route.path} → {status}")
            lines.append(f"Error: {error}")
        else:
            lines.append(
                f"✅ {node.route.method} {node.route.path} "
                f"→ {status} ({latency:.0f}ms)"
            )
            lines.append("")
            lines.append(json.dumps(body, indent=2)[:3000])

        return _text("\n".join(lines))


# --------------------------------------------------------------------------- #
#  Stats helpers                                                               #
# --------------------------------------------------------------------------- #

def _merge_stats(prev: Dict, result: Dict) -> Dict:
    """
    Accumulate per-route call statistics.
    Stored as a separate session, not mixed with HTTP response body.
    """
    calls = prev.get("total_calls", 0) + 1
    errors = prev.get("error_count", 0) + (1 if result.get("_error") else 0)

    # Rolling window of last 50 latency samples
    latencies: List[float] = prev.get("latencies", [])
    lat = result.get("_latency_ms")
    if lat is not None:  # ✅ FIX: Now handles 0.0 correctly
        latencies = (latencies + [lat])[-50:]

    avg_lat = sum(latencies) / len(latencies) if latencies else None

    return {
        "total_calls": calls,
        "error_count": errors,
        "success_rate": round(1 - errors / calls, 3),
"avg_latency_ms": round(avg_lat, 1) if avg_lat is not None else None,
        "last_error": result.get("_error"),
        "last_called_at": time.time(),
        "latencies": latencies,
    }


def _format_context(stats: Dict) -> List[str]:
    """Format stored stats into lines Claude will see before the API call."""
    if not stats or not stats.get("total_calls"):
        return []
    lines = []
    pct = int(stats["success_rate"] * 100)
    lines.append(
        f"📊 {stats['total_calls']} past calls — {pct}% success"
        + (f", avg {stats['avg_latency_ms']:.0f}ms" if stats.get("avg_latency_ms") else "")
    )
    if stats.get("last_error"):
        lines.append(f"⚠️  Last error: {stats['last_error']}")
    return lines


# --------------------------------------------------------------------------- #
#  JSON-RPC helpers                                                            #
# --------------------------------------------------------------------------- #

def _ok(req_id: Any, result: Any) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, msg: str) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": msg}}


def _text(text: str) -> Dict:
    return {"content": [{"type": "text", "text": text}]}


def _log(msg: str) -> None:
    print(f"[agora-code] {msg}", file=sys.stderr, flush=True)