"""
memory_layer.py — AgentMemory: wraps agora-mem for API call tracking and scan caching.

Stores every API call, remembers failures, detects patterns.
Also caches scan results so re-scanning the same target is fast.
Optional — CodebaseAgent works without it, but this is what makes
it smart instead of just a fancy API wrapper.

Requires: pip install agora-code[memory]

Two editions:
  Community:   agora-mem SQLite (local .db file)
  Enterprise:  agora-mem Supabase (cloud, shared across team)
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agora_code.models import RouteCatalog

# --------------------------------------------------------------------------- #
#  AgentMemory                                                                 #
# --------------------------------------------------------------------------- #

class AgentMemory:
    """
    Wraps agora-mem's MemoryStore with API-call-specific methods.

    Usage (community):
        from agora_mem import MemoryStore
        from agora_code.memory_layer import AgentMemory

        store = MemoryStore(storage="sqlite")
        memory = AgentMemory(store)

    Usage (enterprise):
        store = MemoryStore(
            storage="supabase",
            supabase_url=SUPABASE_URL,
            supabase_key=SUPABASE_KEY,
        )
        memory = AgentMemory(store)
    """

    def __init__(self, store: Any):
        """
        Args:
            store: agora_mem.MemoryStore instance
        """
        self._store = store

    # ------------------------------------------------------------------ #
    #  Scan cache                                                          #
    # ------------------------------------------------------------------ #

    async def store_scan_result(
        self,
        target: str,
        catalog: "RouteCatalog",
        ttl_seconds: int = 86400,  # 24h default
    ) -> None:
        """
        Cache a scan result so the same target isn't re-scanned.

        Session key: "scan:{target}"
        Stores: route JSON + extractor tier + TLDR at each level.
        TTL: 24h by default (pass 0 for no expiry).
        """
        from agora_code.tldr import compress_catalog

        session_id = f"scan:{target}"
        state = {
            "target": target,
            "source": catalog.source,
            "extractor": catalog.extractor,
            "route_count": len(catalog.routes),
            "routes_json": catalog.to_json(),
            "tldr_index": compress_catalog(catalog, level="index"),
            "tldr_summary": compress_catalog(catalog, level="summary"),
            "tldr_detail": compress_catalog(catalog, level="detail"),
            "scanned_at": time.time(),
        }
        await self._store.store(
            session_id, state,
            ttl_seconds=ttl_seconds if ttl_seconds > 0 else None,
        )

    async def load_scan_cache(
        self,
        target: str,
    ) -> Optional[Dict]:
        """
        Load a cached scan result by target URL or path.

        Returns the state dict if a fresh cache exists, None otherwise.
        A cache is considered stale if the record has expired (TTL handled
        by agora-mem's MemoryStore automatically).
        """
        session_id = f"scan:{target}"
        record = await self._store.load(session_id)
        if record is None:
            return None
        return record.state

    # ------------------------------------------------------------------ #
    #  Write                                                               #
    # ------------------------------------------------------------------ #

    async def store_api_call(
        self,
        *,
        method: str,
        path: str,
        params: Dict[str, Any],
        response: Any,
        status_code: int,
        latency_ms: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        """
        Store an API call in memory.

        Session key: "{method}:{path}:{timestamp_ms}"
        State includes: all call context for later search & pattern analysis.
        """
        ts = int(time.time() * 1000)
        session_id = f"{method}:{path}:{ts}"

        state = {
            "method": method,
            "path": path,
            "route_key": f"{method} {path}",
            "params": params,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "is_error": status_code >= 400,
            "error": error,
            "response_preview": _truncate(response, 300),
            "timestamp": ts,
        }

        await self._store.store(session_id, state)

    # ------------------------------------------------------------------ #
    #  Read                                                                #
    # ------------------------------------------------------------------ #

    async def recall_similar_calls(
        self,
        method: str,
        path: str,
        query: str = "",
        k: int = 5,
    ) -> List[Dict]:
        """
        Return past calls to this endpoint relevant to `query`.

        Uses semantic search if embeddings are configured,
        falls back to FTS keyword search otherwise.
        """
        search_query = f"{method} {path} {query}".strip()
        try:
            records = await self._store.semantic_search(search_query, k=k)
        except RuntimeError:
            records = await self._store.search(search_query, k=k)

        return [r.state for r in records if r.state.get("path") == path]

    async def get_endpoint_stats(self, method: str, path: str) -> Dict:
        """
        Aggregate stats for one endpoint.

        Returns:
            total_calls, success_rate, avg_latency_ms,
            error_count, last_error, last_called_at
        """
        route_key = f"{method} {path}"
        records = await self._store.search(route_key, k=200)
        calls = [r.state for r in records if r.state.get("route_key") == route_key]

        if not calls:
            return {
                "route": route_key, "total_calls": 0,
                "success_rate": None, "avg_latency_ms": None,
                "error_count": 0, "last_error": None, "last_called_at": None,
            }

        errors = [c for c in calls if c.get("is_error")]
        latencies = [c["latency_ms"] for c in calls if c.get("latency_ms")]
        last = max(calls, key=lambda c: c.get("timestamp", 0))

        return {
            "route": route_key,
            "total_calls": len(calls),
            "success_rate": round(1 - len(errors) / len(calls), 2),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "error_count": len(errors),
            "last_error": errors[-1].get("error") if errors else None,
            "last_called_at": last.get("timestamp"),
        }

    # ------------------------------------------------------------------ #
    #  Pattern detection                                                   #
    # ------------------------------------------------------------------ #

    async def detect_patterns(self, time_window_hours: int = 24) -> List[str]:
        """
        Scan recent calls and return plain-English pattern findings.

        Examples:
          "POST /tickets fails 30% of the time"
          "GET /users called 20x today, all successful"
          "When GET /auth/token fails, POST /orders usually also fails"
        """
        cutoff = (time.time() - time_window_hours * 3600) * 1000
        session_ids = await self._store.list_sessions()

        calls_by_route: Dict[str, List[Dict]] = defaultdict(list)
        for sid in session_ids:
            record = await self._store.load(sid)
            if not record:
                continue
            state = record.state
            if state.get("timestamp", 0) < cutoff:
                continue
            route_key = state.get("route_key")
            if route_key:
                calls_by_route[route_key].append(state)

        findings = []
        for route, calls in calls_by_route.items():
            total = len(calls)
            errors = [c for c in calls if c.get("is_error")]
            error_rate = len(errors) / total if total > 0 else 0

            if total >= 5 and error_rate >= 0.3:
                findings.append(
                    f"⚠️  {route} fails {round(error_rate * 100)}% of the time "
                    f"({len(errors)}/{total} calls)"
                )
            elif total >= 10 and error_rate == 0:
                findings.append(
                    f"✅ {route} called {total}x in last {time_window_hours}h, "
                    f"all successful"
                )
            elif total >= 20:
                findings.append(
                    f"📊 {route} is heavily used: {total} calls, "
                    f"{round((1 - error_rate) * 100)}% success rate"
                )

        # Correlated failures: if A fails and B also fails within 5s
        findings.extend(_detect_correlated_failures(calls_by_route))

        return findings if findings else ["No notable patterns detected yet."]


# --------------------------------------------------------------------------- #
#  Correlation helper                                                          #
# --------------------------------------------------------------------------- #

def _detect_correlated_failures(calls_by_route: Dict[str, List[Dict]]) -> List[str]:
    """Detect pairs of routes that tend to fail together (within 5 seconds)."""
    WINDOW_MS = 5000
    corr: Dict[tuple, int] = defaultdict(int)
    totals: Dict[str, int] = defaultdict(int)

    # Build list of failure timestamps per route
    failures: Dict[str, List[float]] = defaultdict(list)
    for route, calls in calls_by_route.items():
        for c in calls:
            if c.get("is_error"):
                failures[route].append(c.get("timestamp", 0))

    routes = list(failures.keys())
    for i, route_a in enumerate(routes):
        for route_b in routes[i + 1:]:
            for ta in failures[route_a]:
                for tb in failures[route_b]:
                    if abs(ta - tb) <= WINDOW_MS:
                        pair = tuple(sorted([route_a, route_b]))
                        corr[pair] += 1

    findings = []
    for (a, b), count in corr.items():
        if count >= 3:
            findings.append(
                f"🔗 When {a} fails, {b} tends to fail too ({count} co-failures)"
            )
    return findings


# --------------------------------------------------------------------------- #
#  Utility                                                                     #
# --------------------------------------------------------------------------- #

def _truncate(value: Any, max_len: int) -> str:
    try:
        text = json.dumps(value) if not isinstance(value, str) else value
        return text[:max_len] + ("..." if len(text) > max_len else "")
    except Exception:
        return str(value)[:max_len]
