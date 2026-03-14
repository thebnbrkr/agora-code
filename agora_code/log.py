"""
log.py — Centralised logging for agora-code.

Silent by default. Enable with:
    AGORA_LOG_LEVEL=DEBUG    agora-code serve
    AGORA_LOG_LEVEL=WARNING  agora-code serve
    AGORA_LOG_LEVEL=INFO     agora-code serve

Logs go to stderr. If a VectorStore DB path is available, also written to a
`logs` table in SQLite (set AGORA_LOG_DB=1 to enable, default off).
"""
from __future__ import annotations

import logging
import os
import sys
from logging import Handler, LogRecord

# ── Module-level logger ───────────────────────────────────────────────────────

log: logging.Logger = logging.getLogger("agora_code")
log.addHandler(logging.NullHandler())   # silent unless caller configures it


# ── Bootstrap from environment (called once at CLI/server startup) ────────────

def configure(level: str | None = None) -> None:
    """
    Wire up handlers based on environment variables.
    Safe to call multiple times — idempotent.
    """
    if getattr(configure, "_done", False):
        return
    configure._done = True  # type: ignore[attr-defined]

    raw = level or os.environ.get("AGORA_LOG_LEVEL", "")
    if not raw:
        return  # stay silent

    numeric = getattr(logging, raw.upper(), None)
    if not isinstance(numeric, int):
        numeric = logging.WARNING

    log.setLevel(numeric)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    log.addHandler(handler)

    if os.environ.get("AGORA_LOG_DB", "").lower() in ("1", "true", "yes"):
        try:
            db_handler = _SQLiteLogHandler()
            db_handler.setLevel(logging.WARNING)   # only warnings+ to DB
            log.addHandler(db_handler)
        except Exception:
            pass   # DB handler is strictly optional


# ── SQLite log handler ────────────────────────────────────────────────────────

class _SQLiteLogHandler(Handler):
    """Writes WARNING+ log records to the agora-code memory.db logs table."""

    def __init__(self) -> None:
        super().__init__()
        self._conn: object | None = None
        self._init_db()

    def _init_db(self) -> None:
        import sqlite3
        from agora_code.vector_store import DEFAULT_DB
        self._conn = sqlite3.connect(str(DEFAULT_DB), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
                level       TEXT    NOT NULL,
                logger      TEXT    NOT NULL,
                message     TEXT    NOT NULL,
                exc_info    TEXT
            )
        """)
        self._conn.commit()

    def emit(self, record: LogRecord) -> None:
        try:
            msg = self.format(record)
            exc = None
            if record.exc_info:
                import traceback
                exc = "".join(traceback.format_exception(*record.exc_info))
            self._conn.execute(  # type: ignore[union-attr]
                "INSERT INTO logs (level, logger, message, exc_info) VALUES (?,?,?,?)",
                (record.levelname, record.name, msg, exc),
            )
            self._conn.commit()  # type: ignore[union-attr]
        except Exception:
            self.handleError(record)
