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
import struct

# pysqlite3 ships with enable_load_extension unlocked, which is required
# for sqlite-vec on macOS where the system Python disables it.
# Fall back to stdlib sqlite3 on platforms where it works fine (Linux, Windows).
try:
    import pysqlite3 as sqlite3  # type: ignore
except ImportError:
    import sqlite3  # type: ignore
import threading
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
        # Per-thread connections: each thread gets its own sqlite3.Connection so
        # we never share a connection across threads (SQLite connections are not
        # thread-safe for concurrent use).
        self._local = threading.local()
        self._vec_available = False
        self._vec_dim: Optional[int] = None
        self._init_db()

    # ----------------------------------------------------------------------- #
    #  Connection                                                               #
    # ----------------------------------------------------------------------- #

    def _conn_(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            # Each thread's connection must load the sqlite-vec extension
            # independently — extensions are per-connection, not per-file.
            if self._vec_available:
                try:
                    conn.enable_load_extension(True)
                    import sqlite_vec
                    sqlite_vec.load(conn)
                    conn.enable_load_extension(False)
                except Exception:
                    pass
            self._local.conn = conn
        return conn

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
            ("project_id", "TEXT"),  # git remote URL or directory name
        ]:
            try:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {defn}")
            except Exception:
                pass  # Column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_project
            ON sessions(project_id, last_active)
        """)

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
        # Safe migrations for file_changes
        for col, defn in [
            ("project_id",   "TEXT"),
            ("status",       "TEXT DEFAULT 'uncommitted'"),  # uncommitted | committed
            ("committed_at", "TEXT"),
            ("recorded_at_commit_sha", "TEXT"),  # HEAD when we recorded; never updated (commit_sha updated on tag_commit)
        ]:
            try:
                conn.execute(f"ALTER TABLE file_changes ADD COLUMN {col} {defn}")
            except Exception:
                pass
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_changes_project
            ON file_changes(project_id, timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_changes_commit
            ON file_changes(commit_sha, status)
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
            ("branch",     "TEXT"),
            ("files",      "TEXT"),
            ("namespace",  "TEXT DEFAULT 'personal'"),
            ("project_id", "TEXT"),  # git remote URL or directory name
            ("type",       "TEXT DEFAULT 'finding'"),  # decision|finding|blocker|next_step
        ]:
            try:
                conn.execute(f"ALTER TABLE learnings ADD COLUMN {col} {defn}")
            except Exception:
                pass  # Column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_learnings_project
            ON learnings(project_id, timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_learnings_type
            ON learnings(project_id, type, timestamp)
        """)
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

        # ── File snapshots (AST summaries from tree-sitter) ──────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_snapshots (
                id          TEXT PRIMARY KEY,
                file_path   TEXT NOT NULL,
                project_id  TEXT,
                branch      TEXT,
                commit_sha  TEXT,
                session_id  TEXT,
                summary     TEXT NOT NULL,
                symbols     TEXT,
                timestamp   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_file_branch
            ON file_snapshots(project_id, file_path, branch)
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS file_snapshots_fts USING fts5(
                id,
                file_path,
                summary,
                symbols,
                content='file_snapshots',
                content_rowid='rowid'
            )
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS snapshots_ai AFTER INSERT ON file_snapshots BEGIN
                INSERT INTO file_snapshots_fts(rowid, id, file_path, summary, symbols)
                VALUES (new.rowid, new.id, new.file_path, new.summary,
                        COALESCE(new.symbols, ''));
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS snapshots_au AFTER UPDATE ON file_snapshots BEGIN
                INSERT INTO file_snapshots_fts(file_snapshots_fts, rowid, id, file_path, summary, symbols)
                VALUES ('delete', old.rowid, old.id, old.file_path, old.summary,
                        COALESCE(old.symbols, ''));
                INSERT INTO file_snapshots_fts(rowid, id, file_path, summary, symbols)
                VALUES (new.rowid, new.id, new.file_path, new.summary,
                        COALESCE(new.symbols, ''));
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS snapshots_ad AFTER DELETE ON file_snapshots BEGIN
                INSERT INTO file_snapshots_fts(file_snapshots_fts, rowid, id, file_path, summary, symbols)
                VALUES ('delete', old.rowid, old.id, old.file_path, old.summary,
                        COALESCE(old.symbols, ''));
            END
        """)

        # ── Symbol notes (per-function/class one-liners from AST) ────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbol_notes (
                id          TEXT PRIMARY KEY,
                file_path   TEXT NOT NULL,
                symbol_type TEXT NOT NULL,
                symbol_name TEXT NOT NULL,
                start_line  INTEGER,
                end_line    INTEGER,
                signature   TEXT,
                note        TEXT,
                code_block  TEXT,
                project_id  TEXT,
                branch      TEXT,
                commit_sha  TEXT,
                session_id  TEXT,
                timestamp   TEXT NOT NULL
            )
        """)
        # Safe migration for existing DBs
        try:
            conn.execute("ALTER TABLE symbol_notes ADD COLUMN code_block TEXT")
        except Exception:
            pass
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_symbol_notes_unique
            ON symbol_notes(project_id, file_path, symbol_name, branch)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol_notes_file
            ON symbol_notes(project_id, file_path, branch)
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS symbol_notes_fts USING fts5(
                id,
                file_path,
                symbol_name,
                signature,
                note,
                content='symbol_notes',
                content_rowid='rowid'
            )
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS symbol_notes_ai AFTER INSERT ON symbol_notes BEGIN
                INSERT INTO symbol_notes_fts(rowid, id, file_path, symbol_name, signature, note)
                VALUES (new.rowid, new.id, new.file_path, new.symbol_name,
                        COALESCE(new.signature, ''), COALESCE(new.note, ''));
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS symbol_notes_au AFTER UPDATE ON symbol_notes BEGIN
                INSERT INTO symbol_notes_fts(symbol_notes_fts, rowid, id, file_path, symbol_name, signature, note)
                VALUES ('delete', old.rowid, old.id, old.file_path, old.symbol_name,
                        COALESCE(old.signature, ''), COALESCE(old.note, ''));
                INSERT INTO symbol_notes_fts(rowid, id, file_path, symbol_name, signature, note)
                VALUES (new.rowid, new.id, new.file_path, new.symbol_name,
                        COALESCE(new.signature, ''), COALESCE(new.note, ''));
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS symbol_notes_ad AFTER DELETE ON symbol_notes BEGIN
                INSERT INTO symbol_notes_fts(symbol_notes_fts, rowid, id, file_path, symbol_name, signature, note)
                VALUES ('delete', old.rowid, old.id, old.file_path, old.symbol_name,
                        COALESCE(old.signature, ''), COALESCE(old.note, ''));
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

        # ── Commit learnings junction ────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS commit_learnings (
                commit_sha  TEXT NOT NULL,
                learning_id TEXT NOT NULL,
                project_id  TEXT,
                timestamp   TEXT NOT NULL,
                PRIMARY KEY (commit_sha, learning_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_commit_learnings_sha
            ON commit_learnings(commit_sha)
        """)

        # Safe migration: add commit_sha to learnings
        try:
            conn.execute("ALTER TABLE learnings ADD COLUMN commit_sha TEXT")
        except Exception:
            pass
        # Safe migration: add last_injected_at to learnings
        try:
            conn.execute("ALTER TABLE learnings ADD COLUMN last_injected_at TEXT")
        except Exception:
            pass
        # Safe migration: add commit_message to file_changes
        try:
            conn.execute("ALTER TABLE file_changes ADD COLUMN commit_message TEXT")
        except Exception:
            pass

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
        project_id: Optional[str] = None,
    ) -> None:
        """Upsert a session. Pass embedding for semantic session recall."""
        conn = self._conn_()
        now = _now()

        conn.execute("""
            INSERT INTO sessions
                (session_id, started_at, last_active, status, goal,
                 hypothesis, current_action, api_base_url, session_data, tags,
                 project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_active    = excluded.last_active,
                status         = excluded.status,
                goal           = excluded.goal,
                hypothesis     = excluded.hypothesis,
                current_action = excluded.current_action,
                api_base_url   = excluded.api_base_url,
                session_data   = excluded.session_data,
                tags           = excluded.tags,
                project_id     = COALESCE(excluded.project_id, sessions.project_id)
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
            project_id or session.get("project_id"),
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

    def load_most_recent_session(
        self,
        max_age_hours: float = 24.0,
        project_id: Optional[str] = None,
        status: Optional[str] = "in_progress",
    ) -> Optional[Dict]:
        """
        Load the most recently active session, if it's younger than max_age_hours.

        Args:
            max_age_hours: Return None if session is older than this.
            project_id: Filter to sessions from this project (git remote URL).
                        If None, returns the most recent session across all projects.
            status: Filter by status ('in_progress', 'complete', etc.).
                    Pass None to match any status (useful for context recall).
        Returns None if no matching session or if it's stale.
        """
        query = "SELECT session_data, last_active FROM sessions WHERE 1=1"
        params: list = []

        if status is not None:
            query += " AND status = ?"
            params.append(status)

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)

        query += " ORDER BY last_active DESC LIMIT 1"
        row = self._conn_().execute(query, params).fetchone()

        if not row or not row["session_data"]:
            return None

        # Check staleness
        try:
            last = datetime.fromisoformat(row["last_active"])
            age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if age_hours > max_age_hours:
                return None
        except Exception as e:
            from agora_code.log import log
            log.warning("corrupt last_active timestamp in session %r: %s",
                        row.get("session_id", "?"), e)
            return None

        return json.loads(row["session_data"])

    def list_sessions(self, limit: int = 20, project_id: Optional[str] = None) -> List[Dict]:
        """List recent sessions (lightweight — no full session_data)."""
        if project_id:
            rows = self._conn_().execute("""
                SELECT session_id, started_at, last_active, status, goal, tags,
                       branch, commit_sha, ticket, project_id
                FROM sessions
                WHERE project_id = ?
                ORDER BY last_active DESC
                LIMIT ?
            """, (project_id, limit)).fetchall()
        else:
            rows = self._conn_().execute("""
                SELECT session_id, started_at, last_active, status, goal, tags,
                       branch, commit_sha, ticket, project_id
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
        project_id: Optional[str] = None,
    ) -> str:
        """Store a summarized git diff for a file. status defaults to 'uncommitted'.
        recorded_at_commit_sha is set to current HEAD and never updated; commit_sha
        is updated to the new commit when tag_committed_files() runs. Returns record ID."""
        conn = self._conn_()
        fid = str(uuid.uuid4())
        now = _now()
        # Preserve HEAD at record time (tag_commit later updates commit_sha only)
        recorded_sha = commit_sha
        conn.execute("""
            INSERT INTO file_changes
                (id, file_path, diff_summary, diff_snippet, commit_sha,
                 recorded_at_commit_sha, session_id, agent_id, branch, timestamp, project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fid, file_path, diff_summary, diff_snippet, commit_sha, recorded_sha,
              session_id, agent_id, branch, now, project_id))
        conn.commit()
        return fid

    def get_recent_file_changes_for_project(
        self, project_id: str, limit: int = 10
    ) -> List[Dict]:
        """Return recent diff summaries for this project, newest first.
        Queries file_changes.project_id directly — works even before
        checkpoint has been called.
        """
        rows = self._conn_().execute("""
            SELECT file_path, diff_summary, timestamp, status, commit_sha, recorded_at_commit_sha
            FROM file_changes
            WHERE project_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (project_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_file_history(self, file_path: str, limit: int = 20) -> List[Dict]:
        """Return summarized change history for a specific file, newest first."""
        rows = self._conn_().execute("""
            SELECT id, file_path, diff_summary, commit_sha, recorded_at_commit_sha,
                   status, session_id, agent_id AS author, branch, timestamp
            FROM file_changes
            WHERE file_path = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (file_path, limit)).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------------- #
    #  File snapshots (AST summaries)                                          #
    # ----------------------------------------------------------------------- #

    def upsert_file_snapshot(
        self,
        file_path: str,
        summary: str,
        *,
        symbols: Optional[str] = None,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
        commit_sha: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Store or update an AST summary for a file. One record per (project, file, branch)."""
        conn = self._conn_()
        # Check if record exists for this (project, file, branch)
        existing = conn.execute("""
            SELECT id FROM file_snapshots
            WHERE (project_id = ? OR (project_id IS NULL AND ? IS NULL))
              AND file_path = ?
              AND (branch = ? OR (branch IS NULL AND ? IS NULL))
        """, (project_id, project_id, file_path, branch, branch)).fetchone()

        now = _now()
        if existing:
            sid = existing[0]
            conn.execute("""
                UPDATE file_snapshots
                SET summary=?, symbols=?, commit_sha=?, session_id=?, timestamp=?
                WHERE id=?
            """, (summary, symbols, commit_sha, session_id, now, sid))
        else:
            sid = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO file_snapshots
                    (id, file_path, project_id, branch, commit_sha, session_id,
                     summary, symbols, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sid, file_path, project_id, branch, commit_sha, session_id,
                  summary, symbols, now))
        conn.commit()
        return sid

    def search_file_snapshots(
        self,
        query: str,
        k: int = 5,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> List[Dict]:
        """FTS5/BM25 search over file snapshots. Falls back to LIKE."""
        filters = []
        params: list = []
        if project_id:
            filters.append("(s.project_id = ? OR s.project_id IS NULL)")
            params.append(project_id)
        if branch:
            filters.append("(s.branch = ? OR s.branch IS NULL)")
            params.append(branch)
        where_extra = ("AND " + " AND ".join(filters)) if filters else ""

        if not query.strip():
            rows = self._conn_().execute(f"""
                SELECT id, file_path, summary, symbols, branch, commit_sha, timestamp
                FROM file_snapshots s
                WHERE 1=1 {where_extra}
                ORDER BY timestamp DESC LIMIT ?
            """, (*params, k)).fetchall()
            return [dict(r) for r in rows]

        clean = query.replace('"', '""')
        try:
            rows = self._conn_().execute(f"""
                SELECT s.id, s.file_path, s.summary, s.symbols,
                       s.branch, s.commit_sha, s.timestamp,
                       bm25(file_snapshots_fts) as score
                FROM file_snapshots_fts f
                JOIN file_snapshots s ON s.id = f.id
                WHERE file_snapshots_fts MATCH ?
                  {where_extra}
                ORDER BY score LIMIT ?
            """, (f'"{clean}"', *params, k)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            rows = self._conn_().execute(f"""
                SELECT id, file_path, summary, symbols, branch, commit_sha, timestamp
                FROM file_snapshots s
                WHERE (summary LIKE ? OR symbols LIKE ? OR file_path LIKE ?)
                  {where_extra}
                ORDER BY timestamp DESC LIMIT ?
            """, (f"%{query}%", f"%{query}%", f"%{query}%", *params, k)).fetchall()
            return [dict(r) for r in rows]

    def get_file_snapshot(
        self,
        file_path: str,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get the latest AST snapshot for a specific file."""
        row = self._conn_().execute("""
            SELECT id, file_path, summary, symbols, branch, commit_sha, timestamp
            FROM file_snapshots
            WHERE file_path = ?
              AND (project_id = ? OR project_id IS NULL)
              AND (branch = ? OR branch IS NULL)
            ORDER BY timestamp DESC LIMIT 1
        """, (file_path, project_id, branch)).fetchone()
        return dict(row) if row else None


    # ----------------------------------------------------------------------- #
    #  Symbol notes                                                             #
    # ----------------------------------------------------------------------- #

    def upsert_symbol_note(
        self,
        file_path: str,
        symbol_type: str,
        symbol_name: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        signature: Optional[str] = None,
        note: Optional[str] = None,
        code_block: Optional[str] = None,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
        commit_sha: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Insert or update a per-symbol one-liner + code block. Unique per (project, file, symbol, branch)."""
        conn = self._conn_()
        existing = conn.execute("""
            SELECT id FROM symbol_notes
            WHERE (project_id = ? OR (project_id IS NULL AND ? IS NULL))
              AND file_path = ?
              AND symbol_name = ?
              AND (branch = ? OR (branch IS NULL AND ? IS NULL))
        """, (project_id, project_id, file_path, symbol_name, branch, branch)).fetchone()

        now = _now()
        if existing:
            sid = existing[0]
            conn.execute("""
                UPDATE symbol_notes
                SET symbol_type=?, start_line=?, end_line=?, signature=?,
                    note=?, code_block=?, commit_sha=?, session_id=?, timestamp=?
                WHERE id=?
            """, (symbol_type, start_line, end_line, signature,
                  note, code_block, commit_sha, session_id, now, sid))
        else:
            sid = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO symbol_notes
                    (id, file_path, symbol_type, symbol_name, start_line, end_line,
                     signature, note, code_block, project_id, branch, commit_sha, session_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sid, file_path, symbol_type, symbol_name, start_line, end_line,
                  signature, note, code_block, project_id, branch, commit_sha, session_id, now))
        conn.commit()
        return sid

    def upsert_symbol_notes_bulk(self, symbols: list[dict]) -> int:
        """Upsert a list of symbol dicts (same keys as upsert_symbol_note). Returns count."""
        count = 0
        for s in symbols:
            self.upsert_symbol_note(
                s["file_path"], s["symbol_type"], s["symbol_name"],
                start_line=s.get("start_line"),
                end_line=s.get("end_line"),
                signature=s.get("signature"),
                note=s.get("note"),
                code_block=s.get("code_block"),
                project_id=s.get("project_id"),
                branch=s.get("branch"),
                commit_sha=s.get("commit_sha"),
                session_id=s.get("session_id"),
            )
            count += 1
        return count

    def delete_symbols_for_file(
        self,
        file_path: str,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> None:
        """Remove all symbol notes for a file (called before re-indexing after edit)."""
        self._conn_().execute("""
            DELETE FROM symbol_notes
            WHERE file_path = ?
              AND (project_id = ? OR project_id IS NULL)
              AND (branch = ? OR branch IS NULL)
        """, (file_path, project_id, branch))
        self._conn_().commit()

    def get_symbols_for_file(
        self,
        file_path: str,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> List[Dict]:
        """Return all symbol notes for a file, ordered by start_line."""
        rows = self._conn_().execute("""
            SELECT id, file_path, symbol_type, symbol_name, start_line, end_line,
                   signature, note, branch, commit_sha, timestamp
            FROM symbol_notes
            WHERE file_path = ?
              AND (project_id = ? OR project_id IS NULL)
              AND (branch = ? OR branch IS NULL)
            ORDER BY start_line
        """, (file_path, project_id, branch)).fetchall()
        return [dict(r) for r in rows]

    def search_symbol_notes(
        self,
        query: str,
        k: int = 10,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
        symbol_type: Optional[str] = None,
    ) -> List[Dict]:
        """FTS5/BM25 search over symbol notes. Falls back to LIKE."""
        filters = []
        params: list = []
        if project_id:
            filters.append("(s.project_id = ? OR s.project_id IS NULL)")
            params.append(project_id)
        if branch:
            filters.append("(s.branch = ? OR s.branch IS NULL)")
            params.append(branch)
        if symbol_type:
            filters.append("s.symbol_type = ?")
            params.append(symbol_type)
        where_extra = ("AND " + " AND ".join(filters)) if filters else ""

        if not query.strip():
            rows = self._conn_().execute(f"""
                SELECT id, file_path, symbol_type, symbol_name, start_line, end_line,
                       signature, note, branch, commit_sha, timestamp
                FROM symbol_notes s
                WHERE 1=1 {where_extra}
                ORDER BY file_path, start_line LIMIT ?
            """, (*params, k)).fetchall()
            return [dict(r) for r in rows]

        # Multi-word queries: join tokens with OR so "store learning" matches
        # symbols containing any of the words, not the exact phrase.
        tokens = [t.replace('"', '""') for t in query.split() if t]
        fts_expr = " OR ".join(f'"{t}"' for t in tokens) if tokens else f'"{query}"'
        try:
            rows = self._conn_().execute(f"""
                SELECT s.id, s.file_path, s.symbol_type, s.symbol_name,
                       s.start_line, s.end_line, s.signature, s.note,
                       s.branch, s.commit_sha, s.timestamp,
                       bm25(symbol_notes_fts) as score
                FROM symbol_notes_fts f
                JOIN symbol_notes s ON s.id = f.id
                WHERE symbol_notes_fts MATCH ?
                  {where_extra}
                ORDER BY score LIMIT ?
            """, (fts_expr, *params, k)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            rows = self._conn_().execute(f"""
                SELECT id, file_path, symbol_type, symbol_name, start_line, end_line,
                       signature, note, branch, commit_sha, timestamp
                FROM symbol_notes s
                WHERE (symbol_name LIKE ? OR note LIKE ? OR file_path LIKE ?)
                  {where_extra}
                ORDER BY file_path, start_line LIMIT ?
            """, (f"%{query}%", f"%{query}%", f"%{query}%", *params, k)).fetchall()
            return [dict(r) for r in rows]

    def list_recent_symbol_notes_with_blocks(
        self, limit: int = 10, project_id: Optional[str] = None
    ) -> List[Dict]:
        """Return recent symbol_notes including code_block (for memory --verbose)."""
        params: list = []
        where = ""
        if project_id:
            where = " AND (project_id = ? OR project_id IS NULL)"
            params.append(project_id)
        params.append(limit)
        rows = self._conn_().execute(f"""
            SELECT id, file_path, symbol_type, symbol_name, start_line, end_line,
                   signature, note, code_block, timestamp
            FROM symbol_notes
            WHERE 1=1 {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]

    def tag_committed_files(
        self,
        file_paths: list[str],
        commit_sha: str,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> int:
        """Mark file_changes as committed and set commit_sha on symbol_notes + file_snapshots.
        Called from post-commit hook (e.g. track-diff after git commit). Returns file_changes rows updated."""
        conn = self._conn_()
        now = _now()
        updated = 0
        for fp in file_paths:
            like_pat = f"%{fp}"
            r = conn.execute("""
                UPDATE file_changes
                SET commit_sha=?, status='committed', committed_at=?,
                    diff_summary = CASE
                        WHEN diff_summary NOT LIKE '%#kept%' AND diff_summary NOT LIKE '%#not_kept%'
                        THEN diff_summary || ' #kept'
                        ELSE diff_summary
                    END
                WHERE file_path LIKE ? AND status='uncommitted'
                  AND (project_id=? OR project_id IS NULL)
            """, (commit_sha, now, like_pat, project_id))
            updated += r.rowcount
            conn.execute("""
                UPDATE symbol_notes
                SET commit_sha=?
                WHERE file_path LIKE ?
                  AND (project_id=? OR project_id IS NULL)
                  AND (branch=? OR branch IS NULL)
            """, (commit_sha, like_pat, project_id, branch))
            conn.execute("""
                UPDATE file_snapshots
                SET commit_sha=?
                WHERE file_path LIKE ?
                  AND (project_id=? OR project_id IS NULL)
                  AND (branch=? OR branch IS NULL)
            """, (commit_sha, like_pat, project_id, branch))
        conn.commit()
        return updated

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
        project_id: Optional[str] = None,
        type: str = "finding",  # decision|finding|blocker|next_step
        commit_sha: Optional[str] = None,
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
                 branch, files, namespace, project_id, type, commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lid, session_id, now, api_base_url, endpoint_method,
            endpoint_path, finding, evidence, confidence, tags_json,
            branch, files_json, namespace, project_id, type, commit_sha,
        ))

        if embedding and self._vec_available:
            dim = len(embedding)
            self._ensure_vec_tables(dim)
            conn.execute(f"""
                INSERT OR REPLACE INTO learnings_vec_{dim} (learning_id, embedding)
                VALUES (?, ?)
            """, (lid, self._pack(embedding)))

        conn.commit()

        # Link to commit if provided
        if commit_sha:
            self.link_learning_to_commit(lid, commit_sha, project_id=project_id)

        return lid

    def link_learning_to_commit(
        self,
        learning_id: str,
        commit_sha: str,
        project_id: Optional[str] = None,
    ) -> None:
        """Create a commit_learnings junction entry."""
        conn = self._conn_()
        conn.execute("""
            INSERT OR IGNORE INTO commit_learnings (commit_sha, learning_id, project_id, timestamp)
            VALUES (?, ?, ?, ?)
        """, (commit_sha, learning_id, project_id, _now()))
        conn.commit()

    def get_learnings_for_commit(
        self,
        commit_sha: str,
        project_id: Optional[str] = None,
    ) -> List[Dict]:
        """Return all learnings linked to a specific commit."""
        filters = ["cl.commit_sha = ?"]
        params: list = [commit_sha]
        if project_id:
            filters.append("(l.project_id = ? OR l.project_id IS NULL)")
            params.append(project_id)
        where = " AND ".join(filters)
        rows = self._conn_().execute(f"""
            SELECT l.id, l.finding, l.evidence, l.confidence, l.tags,
                   l.type, l.branch, l.files, l.timestamp, l.commit_sha
            FROM commit_learnings cl
            JOIN learnings l ON l.id = cl.learning_id
            WHERE {where}
            ORDER BY l.timestamp
        """, params).fetchall()
        return [dict(r) for r in rows]

    def get_learnings_for_commits(
        self,
        commit_shas: List[str],
        project_id: Optional[str] = None,
        limit: int = 12,
    ) -> List[Dict]:
        """Return learnings for a list of commits, ordered by recency."""
        if not commit_shas:
            return []
        placeholders = ",".join("?" * len(commit_shas))
        params: list = list(commit_shas)
        extra = ""
        if project_id:
            extra = "AND (l.project_id = ? OR l.project_id IS NULL)"
            params.append(project_id)
        rows = self._conn_().execute(f"""
            SELECT l.id, l.finding, l.evidence, l.confidence, l.tags,
                   l.type, l.branch, l.files, l.timestamp, l.commit_sha
            FROM commit_learnings cl
            JOIN learnings l ON l.id = cl.learning_id
            WHERE cl.commit_sha IN ({placeholders}) {extra}
            ORDER BY l.timestamp DESC
            LIMIT ?
        """, (*params, limit)).fetchall()
        return [dict(r) for r in rows]

    def mark_learnings_injected(self, learning_ids: List[str]) -> None:
        """Update last_injected_at for a batch of learning IDs."""
        if not learning_ids:
            return
        now = _now()
        placeholders = ",".join("?" * len(learning_ids))
        self._conn_().execute(
            f"UPDATE learnings SET last_injected_at = ? WHERE id IN ({placeholders})",
            (now, *learning_ids),
        )
        self._conn_().commit()

    def get_file_changes_for_commit(
        self,
        file_path: str,
        commit_sha: str,
        project_id: Optional[str] = None,
    ) -> List[Dict]:
        """Return all file_changes rows for a file tagged with a specific commit SHA."""
        like_pat = f"%{file_path}"
        rows = self._conn_().execute("""
            SELECT id, file_path, diff_summary, commit_sha, timestamp
            FROM file_changes
            WHERE file_path LIKE ? AND commit_sha = ?
              AND (project_id = ? OR project_id IS NULL)
            ORDER BY timestamp ASC
        """, (like_pat, commit_sha, project_id)).fetchall()
        return [dict(r) for r in rows]

    def get_uncommitted_file_changes(
        self,
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Return uncommitted file_changes for current project/branch."""
        filters = ["status = 'uncommitted'"]
        params: list = []
        if project_id:
            filters.append("project_id = ?")
            params.append(project_id)
        if branch:
            filters.append("branch = ?")
            params.append(branch)
        where = " AND ".join(filters)
        rows = self._conn_().execute(f"""
            SELECT file_path, diff_summary, diff_snippet, commit_sha,
                   recorded_at_commit_sha, timestamp
            FROM file_changes
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """, (*params, limit)).fetchall()
        return [dict(r) for r in rows]

    def search_learnings_semantic(
        self,
        query_embedding: list[float],
        k: int = 5,
        namespace: str = "personal",
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
        type: Optional[str] = None,
    ) -> List[Dict]:
        """Cosine similarity search over learnings. Returns [] if sqlite-vec unavailable."""
        if not self._vec_available or not self._vec_dim:
            return []

        dim = len(query_embedding)
        if self._vec_dim != dim:
            return []

        filters = ["(l.namespace = ? OR l.namespace IS NULL)"]
        params: list = [self._pack(query_embedding), k * 2, namespace]

        if project_id:
            filters.append("(l.project_id = ? OR l.project_id IS NULL)")
            params.append(project_id)
        if branch:
            filters.append("(l.branch = ? OR l.branch IS NULL)")
            params.append(branch)
        if type:
            filters.append("l.type = ?")
            params.append(type)

        where = " AND ".join(filters)

        try:
            rows = self._conn_().execute(f"""
                SELECT l.id, l.finding, l.evidence, l.confidence, l.tags,
                       l.endpoint_method, l.endpoint_path, l.timestamp,
                       l.branch, l.files, l.namespace, l.type,
                       v.distance
                FROM learnings_vec_{dim} v
                JOIN learnings l ON l.id = v.learning_id
                WHERE v.embedding MATCH ? AND k = ?
                  AND {where}
                ORDER BY v.distance
            """, params).fetchall()

            return [_learning_row(r) for r in rows[:k]]
        except Exception as e:
            from agora_code.log import log
            log.warning("semantic search failed: %s", e)
            return []

    def search_learnings_keyword(
        self,
        query: str,
        k: int = 5,
        namespace: str = "personal",
        project_id: Optional[str] = None,
        branch: Optional[str] = None,
        type: Optional[str] = None,
    ) -> List[Dict]:
        """FTS5/BM25 keyword search over learnings. Always works."""
        SELECT_COLS = """id, finding, evidence, confidence, tags,
                         endpoint_method, endpoint_path, timestamp,
                         branch, files, namespace, type, commit_sha"""

        # Build dynamic WHERE filters
        filters = ["(namespace = ? OR namespace IS NULL)"]
        base_params: list = [namespace]
        if project_id:
            filters.append("(project_id = ? OR project_id IS NULL)")
            base_params.append(project_id)
        if branch:
            filters.append("(branch = ? OR branch IS NULL)")
            base_params.append(branch)
        if type:
            filters.append("type = ?")
            base_params.append(type)
        where = " AND ".join(filters)

        if not query.strip():
            # No query — return recent learnings ordered by recency
            rows = self._conn_().execute(f"""
                SELECT {SELECT_COLS}
                FROM learnings
                WHERE {where}
                ORDER BY timestamp DESC LIMIT ?
            """, (*base_params, k)).fetchall()
            return [_learning_row(r) for r in rows]

        clean = query.replace('"', '""')
        try:
            fts_params = [f'"{clean}"', *base_params, k]
            # FTS5 content table — join back to get all columns including type
            fts_filters = " AND ".join(
                f.replace("namespace", "l.namespace")
                 .replace("project_id", "l.project_id")
                 .replace("branch", "l.branch")
                 .replace("type", "l.type")
                for f in filters
            )
            rows = self._conn_().execute(f"""
                SELECT l.id, l.finding, l.evidence, l.confidence, l.tags,
                       l.endpoint_method, l.endpoint_path, l.timestamp,
                       l.branch, l.files, l.namespace, l.type, l.commit_sha,
                       bm25(learnings_fts) as score
                FROM learnings_fts f
                JOIN learnings l ON l.id = f.id
                WHERE learnings_fts MATCH ?
                  AND {fts_filters}
                ORDER BY score
                LIMIT ?
            """, fts_params).fetchall()
            return [_learning_row(r) for r in rows]
        except Exception:
            # FTS5 match failed — fall back to LIKE using same dynamic filters
            like_params = [f"%{query}%", f"%{query}%", *base_params, k]
            rows = self._conn_().execute(f"""
                SELECT id, finding, evidence, confidence, tags,
                       endpoint_method, endpoint_path, timestamp,
                       branch, files, namespace, type, commit_sha
                FROM learnings
                WHERE (finding LIKE ? OR evidence LIKE ?)
                  AND {where}
                ORDER BY timestamp DESC LIMIT ?
            """, like_params).fetchall()
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

    def list_recent_api_calls(self, limit: int = 20) -> List[Dict]:
        """Return recent API calls (method, path, status, latency), newest first."""
        rows = self._conn_().execute("""
            SELECT session_id, timestamp, method, path,
                   response_status, latency_ms, success, error_message
            FROM api_calls
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> Dict:
        conn = self._conn_()
        return {
            "sessions":        conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "learnings":       conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0],
            "api_calls":       conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0],
            "file_snapshots":  conn.execute("SELECT COUNT(*) FROM file_snapshots").fetchone()[0],
            "symbol_notes":    conn.execute("SELECT COUNT(*) FROM symbol_notes").fetchone()[0],
            "vector_search":   self._vec_available,
            "db_path":         str(self.db_path),
        }

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

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
    d.setdefault("type", "finding")
    d.pop("score", None)
    d.pop("distance", None)
    return d


# --------------------------------------------------------------------------- #
#  Module-level singleton                                                      #
# --------------------------------------------------------------------------- #

_store: Optional[VectorStore] = None
_store_lock = threading.Lock()


def get_store(db_path: Optional[str] = None) -> VectorStore:
    """Return the global VectorStore singleton (thread-safe)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:  # double-checked: re-test inside the lock
                _store = VectorStore(db_path)
    return _store
