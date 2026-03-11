"""
vector_store.py — SQLite + sqlite-vec + FTS5 storage for agora-code.

Stores three types of data, all in one local DB:
  - sessions      : your API testing session state (replaces any YAML handoff)
  - learnings     : permanent "this API does X" knowledge base
  - api_calls     : per-call HTTP interaction log for pattern detection

DB path (in priority order):
  1. Explicit path passed to VectorStore(db_path=...)
  2. AGORA_CODE_DB env var
  3. ~/.agora-code/memory.db   (global — learnings persist across projects)

Vector search  : sqlite-vec (optional dep) — cosine similarity
Keyword search : FTS5 / BM25 — always available, zero extra deps
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
#  Config                                                                      #
# --------------------------------------------------------------------------- #

DEFAULT_DB = Path.home() / ".agora-code" / "memory.db"


# --------------------------------------------------------------------------- #
#  VectorStore                                                                 #
# --------------------------------------------------------------------------- #

class VectorStore:
    """
    Local SQLite-backed store for sessions, learnings, and API interactions.

    Uses sqlite-vec for vector similarity when available; gracefully falls
    back to FTS5/BM25 keyword search so zero-config installs still work.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path).expanduser()
        else:
            env = os.environ.get("AGORA_CODE_DB")
            self.db_path = Path(env).expanduser() if env else DEFAULT_DB

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._vec_available = False
        self._vec_dim: Optional[int] = None
        self._init_db()

    # ----------------------------------------------------------------------- #
    #  Connection                                                               #
    # ----------------------------------------------------------------------- #

    def _conn_(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    # ----------------------------------------------------------------------- #
    #  Schema bootstrap                                                         #
    # ----------------------------------------------------------------------- #

    def _init_db(self):
        conn = self._conn_()

        # Try to load sqlite-vec
        try:
            conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            self._vec_available = True
        except Exception:
            self._vec_available = False

        # ── Sessions ────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id     TEXT PRIMARY KEY,
                started_at     TEXT NOT NULL,
                last_active    TEXT NOT NULL,
                status         TEXT DEFAULT 'in_progress',
                goal           TEXT,
                hypothesis     TEXT,
                current_action TEXT,
                api_base_url   TEXT,
                session_data   TEXT,
                tags           TEXT
            )
        """)
        # Safe migrations for sessions table
        for col, defn in [
            ("branch",     "TEXT"),
            ("commit_sha", "TEXT"),
            ("ticket",     "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {defn}")
            except Exception:
                pass  # Column already exists

        # ── File changes (git diff tracking) ────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_changes (
                id           TEXT PRIMARY KEY,
                file_path    TEXT NOT NULL,
                diff_summary TEXT,
                diff_snippet TEXT,
                commit_sha   TEXT,
                session_id   TEXT,
                agent_id     TEXT,
                branch       TEXT,
                timestamp    TEXT NOT NULL
            )
        """)

        # ── Learnings ────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learnings (
                id              TEXT PRIMARY KEY,
                session_id      TEXT,
                timestamp       TEXT NOT NULL,
                api_base_url    TEXT,
                endpoint_method TEXT,
                endpoint_path   TEXT,
                finding         TEXT NOT NULL,
                evidence        TEXT,
                confidence      TEXT DEFAULT 'confirmed',
                tags            TEXT,
                branch          TEXT,
                files           TEXT,
                namespace       TEXT DEFAULT 'personal'
            )
        """)
        # Safe migrations for existing DBs — ADD COLUMN IF NOT EXISTS
        for col, defn in [
            ("branch",    "TEXT"),
            ("files",     "TEXT"),
            ("namespace", "TEXT DEFAULT 'personal'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE learnings ADD COLUMN {col} {defn}")
            except Exception:
                pass  # Column already exists
        # FTS5 over learnings — always available
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS learnings_fts USING fts5(
                id,
                finding,
                evidence,
                tags,
                content='learnings',
                content_rowid='rowid'
            )
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS learnings_ai AFTER INSERT ON learnings BEGIN
                INSERT INTO learnings_fts(rowid, id, finding, evidence, tags)
                VALUES (new.rowid, new.id, new.finding,
                        COALESCE(new.evidence, ''),
                        COALESCE(new.tags, ''));
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS learnings_ad AFTER DELETE ON learnings BEGIN
                INSERT INTO learnings_fts(learnings_fts, rowid, id, finding, evidence, tags)
                VALUES ('delete', old.rowid, old.id, old.finding,
                        COALESCE(old.evidence, ''),
                        COALESCE(old.tags, ''));
            END
        """)

        # ── API interactions ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_calls (
                id              TEXT PRIMARY KEY,
                session_id      TEXT,
                timestamp       TEXT NOT NULL,
                method          TEXT,
                path            TEXT,
                request_params  TEXT,
                response_status INTEGER,
                latency_ms      REAL,
                success         INTEGER,
                error_message   TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calls_path
            ON api_calls(path, method, success)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calls_session
            ON api_calls(session_id, timestamp)
        """)

        conn.commit()

    # ----------------------------------------------------------------------- #
    #  Vector table helpers (created lazily per-dim)                           #
    # ----------------------------------------------------------------------- #

    def _ensure_vec_tables(self, dim: int):
        """Create sqlite-vec virtual tables for the embedding dimension."""
        if not self._vec_available:
            return
        if self._vec_dim == dim:
            return

        conn = self._conn_()
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_vec_{dim} USING vec0(
                session_id TEXT PRIMARY KEY,
                embedding  float[{dim}]
            )
        """)
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS learnings_vec_{dim} USING vec0(
                learning_id TEXT PRIMARY KEY,
                embedding   float[{dim}]
            )
        """)
        conn.commit()
        self._vec_dim = dim

    @staticmethod
    def _pack(vec: list[float]) -> bytes:
        return struct.pack(f"{len(vec)}f", *vec)

    # ----------------------------------------------------------------------- #
    #  Sessions                                                                 #
    # ----------------------------------------------------------------------- #

    def save_session(
        self,
        session: Dict[str, Any],
        embedding: Optional[list[float]] = None,
    ) -> None:
        """Upsert a session. Pass embedding for semantic session recall."""
        conn = self._conn_()
        now = _now()

        conn.execute("""
            INSERT INTO sessions
                (session_id, started_at, last_active, status, goal,
                 hypothesis, current_action, api_base_url, session_data, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_active    = excluded.last_active,
                status         = excluded.status,
                goal           = excluded.goal,
                hypothesis     = excluded.hypothesis,
                current_action = excluded.current_action,
                api_base_url   = excluded.api_base_url,
                session_data   = excluded.session_data,
                tags           = excluded.tags
        """, (
            session["session_id"],
            session.get("started_at", now),
            now,
            session.get("status", "in_progress"),
            session.get("goal"),
            session.get("hypothesis"),
            session.get("current_action"),
            session.get("api_base_url"),
            json.dumps(session),
            json.dumps(session.get("tags", [])),
        ))

        if embedding and self._vec_available:
            dim = len(embedding)
            self._ensure_vec_tables(dim)
            conn.execute(f"""
                INSERT OR REPLACE INTO sessions_vec_{dim} (session_id, embedding)
                VALUES (?, ?)
            """, (session["session_id"], self._pack(embedding)))

        conn.commit()

    def load_session(self, session_id: str) -> Optional[Dict]:
        """Load a session by ID."""
        row = self._conn_().execute(
            "SELECT session_data FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row and row["session_data"]:
            return json.loads(row["session_data"])
        return None

    def load_most_recent_session(self, max_age_hours: float = 24.0) -> Optional[Dict]:
        """
        Load the most recently active session, if it's younger than max_age_hours.
        Returns None if no session or if it's stale.
        """
        row = self._conn_().execute("""
            SELECT session_data, last_active FROM sessions
            WHERE status = 'in_progress'
            ORDER BY last_active DESC
            LIMIT 1
        """).fetchone()

        if not row or not row["session_data"]:
            return None

        # Check staleness
        try:
            last = datetime.fromisoformat(row["last_active"])
            age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if age_hours > max_age_hours:
                return None
        except Exception:
            return None

        return json.loads(row["session_data"])

    def list_sessions(self, limit: int = 20) -> List[Dict]:
        """List recent sessions (lightweight — no full session_data)."""
        rows = self._conn_().execute("""
            SELECT session_id, started_at, last_active, status, goal, tags,
                   branch, commit_sha, ticket
            FROM sessions
            ORDER BY last_active DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------------- #
    #  File change tracking                                                     #
    # ----------------------------------------------------------------------- #

    def save_file_change(
        self,
        file_path: str,
        diff_summary: str,
        *,
        diff_snippet: Optional[str] = None,
        commit_sha: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> str:
        """Store a summarized git diff for a file. Returns record ID."""
        conn = self._conn_()
        fid = str(uuid.uuid4())
        now = _now()
        conn.execute("""
            INSERT INTO file_changes
                (id, file_path, diff_summary, diff_snippet, commit_sha,
                 session_id, agent_id, branch, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fid, file_path, diff_summary, diff_snippet, commit_sha,
              session_id, agent_id, branch, now))
        conn.commit()
        return fid

    def get_file_history(self, file_path: str, limit: int = 20) -> List[Dict]:
        """Return summarized change history for a specific file, newest first."""
        rows = self._conn_().execute("""
            SELECT id, file_path, diff_summary, commit_sha, session_id,
                   agent_id AS author, branch, timestamp
            FROM file_changes
            WHERE file_path = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (file_path, limit)).fetchall()
        return [dict(r) for r in rows]


    # ----------------------------------------------------------------------- #
    #  Learnings                                                                #
    # ----------------------------------------------------------------------- #

    def store_learning(
        self,
        finding: str,
        *,
        session_id: Optional[str] = None,
        api_base_url: Optional[str] = None,
        endpoint_method: Optional[str] = None,
        endpoint_path: Optional[str] = None,
        evidence: Optional[str] = None,
        confidence: str = "confirmed",
        tags: Optional[list[str]] = None,
        embedding: Optional[list[float]] = None,
        branch: Optional[str] = None,
        files: Optional[list[str]] = None,
        namespace: str = "personal",
    ) -> str:
        """Store a learning and (optionally) its embedding. Returns learning ID."""
        conn = self._conn_()
        lid = str(uuid.uuid4())
        now = _now()
        tags_json = json.dumps(tags or [])
        files_json = json.dumps(files or [])

        conn.execute("""
            INSERT INTO learnings
                (id, session_id, timestamp, api_base_url, endpoint_method,
                 endpoint_path, finding, evidence, confidence, tags,
                 branch, files, namespace)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lid, session_id, now, api_base_url, endpoint_method,
            endpoint_path, finding, evidence, confidence, tags_json,
            branch, files_json, namespace,
        ))

        if embedding and self._vec_available:
            dim = len(embedding)
            self._ensure_vec_tables(dim)
            conn.execute(f"""
                INSERT OR REPLACE INTO learnings_vec_{dim} (learning_id, embedding)
                VALUES (?, ?)
            """, (lid, self._pack(embedding)))

        conn.commit()
        return lid

    def search_learnings_semantic(
        self,
        query_embedding: list[float],
        k: int = 5,
        namespace: str = "personal",
    ) -> List[Dict]:
        """Cosine similarity search over learnings. Returns [] if sqlite-vec unavailable."""
        if not self._vec_available or not self._vec_dim:
            return []

        dim = len(query_embedding)
        if self._vec_dim != dim:
            return []

        try:
            rows = self._conn_().execute(f"""
                SELECT l.id, l.finding, l.evidence, l.confidence, l.tags,
                       l.endpoint_method, l.endpoint_path, l.timestamp,
                       l.branch, l.files, l.namespace,
                       v.distance
                FROM learnings_vec_{dim} v
                JOIN learnings l ON l.id = v.learning_id
                WHERE v.embedding MATCH ? AND k = ?
                  AND (l.namespace = ? OR l.namespace IS NULL)
                ORDER BY v.distance
            """, (self._pack(query_embedding), k * 2, namespace)).fetchall()

            return [_learning_row(r) for r in rows[:k]]
        except Exception:
            return []

    def search_learnings_keyword(
        self,
        query: str,
        k: int = 5,
        namespace: str = "personal",
    ) -> List[Dict]:
        """FTS5/BM25 keyword search over learnings. Always works."""
        if not query.strip():
            # No query — return recent
            rows = self._conn_().execute("""
                SELECT id, finding, evidence, confidence, tags,
                       endpoint_method, endpoint_path, timestamp,
                       branch, files, namespace
                FROM learnings
                WHERE (namespace = ? OR namespace IS NULL)
                ORDER BY timestamp DESC LIMIT ?
            """, (namespace, k)).fetchall()
            return [_learning_row(r) for r in rows]

        clean = query.replace('"', '""')
        try:
            rows = self._conn_().execute("""
                SELECT l.id, l.finding, l.evidence, l.confidence, l.tags,
                       l.endpoint_method, l.endpoint_path, l.timestamp,
                       l.branch, l.files, l.namespace,
                       bm25(learnings_fts) as score
                FROM learnings_fts f
                JOIN learnings l ON l.id = f.id
                WHERE learnings_fts MATCH ?
                  AND (l.namespace = ? OR l.namespace IS NULL)
                ORDER BY score
                LIMIT ?
            """, (f'"{clean}"', namespace, k)).fetchall()
            return [_learning_row(r) for r in rows]
        except Exception:
            # FTS5 match failed — fall back to LIKE
            rows = self._conn_().execute("""
                SELECT id, finding, evidence, confidence, tags,
                       endpoint_method, endpoint_path, timestamp,
                       branch, files, namespace
                FROM learnings
                WHERE (finding LIKE ? OR evidence LIKE ?)
                  AND (namespace = ? OR namespace IS NULL)
                ORDER BY timestamp DESC LIMIT ?
            """, (f"%{query}%", f"%{query}%", namespace, k)).fetchall()
            return [_learning_row(r) for r in rows]

    # ----------------------------------------------------------------------- #
    #  API call log                                                             #
    # ----------------------------------------------------------------------- #

    def log_api_call(
        self,
        *,
        session_id: Optional[str] = None,
        method: str,
        path: str,
        request_params: Optional[dict] = None,
        response_status: int,
        latency_ms: float = 0.0,
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """Append one API call to the interaction log."""
        conn = self._conn_()
        conn.execute("""
            INSERT INTO api_calls
                (id, session_id, timestamp, method, path,
                 request_params, response_status, latency_ms, success, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            session_id,
            _now(),
            method.upper(),
            path,
            json.dumps(request_params or {}),
            response_status,
            latency_ms,
            1 if success else 0,
            error_message,
        ))
        conn.commit()

    def get_endpoint_stats(self, method: str, path: str) -> Dict:
        """Aggregate stats for one endpoint over time."""
        row = self._conn_().execute("""
            SELECT
                COUNT(*)                                        AS total,
                SUM(success)                                    AS successes,
                AVG(latency_ms)                                 AS avg_latency,
                MAX(timestamp)                                  AS last_called,
                MAX(CASE WHEN success=0 THEN error_message END) AS last_error
            FROM api_calls
            WHERE method = ? AND path = ?
        """, (method.upper(), path)).fetchone()

        if not row or row["total"] == 0:
            return {"total": 0, "success_rate": None, "avg_latency_ms": None}

        total = row["total"]
        succ  = row["successes"] or 0
        return {
            "total":        total,
            "success_rate": round(succ / total, 3),
            "avg_latency_ms": round(row["avg_latency"] or 0, 1),
            "last_called":  row["last_called"],
            "last_error":   row["last_error"],
        }

    def get_failure_patterns(
        self,
        path: str,
        min_occurrences: int = 3,
        window_hours: float = 168.0,  # 7 days
    ) -> List[Dict]:
        """
        Find parameter combos that repeatedly fail for this endpoint.
        Returns suggestions for the MCP tool call context.
        """
        rows = self._conn_().execute("""
            SELECT request_params, COUNT(*) as n,
                   AVG(CAST(success AS FLOAT)) as sr
            FROM api_calls
            WHERE path = ?
              AND timestamp > datetime('now', ?)
            GROUP BY request_params
            HAVING n >= ? AND sr < 0.3
            ORDER BY n DESC
            LIMIT 5
        """, (path, f"-{window_hours} hours", min_occurrences)).fetchall()

        return [
            {
                "params": json.loads(r["request_params"]),
                "occurrences": r["n"],
                "success_rate": round(r["sr"], 2),
                "suggestion": "These params consistently fail — check your learnings.",
            }
            for r in rows
        ]

    # ----------------------------------------------------------------------- #
    #  Misc                                                                     #
    # ----------------------------------------------------------------------- #

    def get_stats(self) -> Dict:
        conn = self._conn_()
        return {
            "sessions":        conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "learnings":       conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0],
            "api_calls":       conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0],
            "vector_search":   self._vec_available,
            "db_path":         str(self.db_path),
        }

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _learning_row(row) -> Dict:
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except Exception:
        d["tags"] = []
    try:
        d["files"] = json.loads(d.get("files") or "[]")
    except Exception:
        d["files"] = []
    d.pop("score", None)
    d.pop("distance", None)
    return d


# --------------------------------------------------------------------------- #
#  Module-level singleton                                                      #
# --------------------------------------------------------------------------- #

_store: Optional[VectorStore] = None


def get_store(db_path: Optional[str] = None) -> VectorStore:
    """Return the global VectorStore singleton."""
    global _store
    if _store is None:
        _store = VectorStore(db_path)
    return _store
