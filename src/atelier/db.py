"""SQLite index and ledger.

The files under runs/ are the source of truth; this database is a queryable
cache. `atelier reindex` rebuilds it from disk, so losing it costs nothing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    problem             TEXT NOT NULL,
    harness             TEXT NOT NULL,
    harness_version     INTEGER,
    provider            TEXT NOT NULL,
    account             TEXT,
    model               TEXT,
    effort              TEXT,
    mode                TEXT NOT NULL,
    provider_session_id TEXT,
    parent_run_id       TEXT,
    bench_id            TEXT,
    label               TEXT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    status              TEXT NOT NULL,
    ended_reason        TEXT,
    wall_seconds        REAL,
    active_seconds      REAL,
    turns               INTEGER DEFAULT 0,
    tool_calls          INTEGER DEFAULT 0,
    tok_in              INTEGER DEFAULT 0,
    tok_out             INTEGER DEFAULT 0,
    tok_cache_read      INTEGER DEFAULT 0,
    tok_cache_write     INTEGER DEFAULT 0,
    cost_computed       REAL DEFAULT 0.0,
    cost_reported       REAL,
    status_before       TEXT,
    status_after        TEXT,
    git_commit          TEXT,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS usage_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    agent_label     TEXT,
    ts              TEXT NOT NULL,
    account         TEXT,
    model           TEXT,
    tok_in          INTEGER DEFAULT 0,
    tok_out         INTEGER DEFAULT 0,
    tok_cache_read  INTEGER DEFAULT 0,
    tok_cache_write INTEGER DEFAULT 0,
    cost            REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS account_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,            -- rate_limited | probe | auth_error | note
    payload TEXT
);

CREATE TABLE IF NOT EXISTS steering (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    ts           TEXT NOT NULL,
    text         TEXT NOT NULL,
    origin       TEXT DEFAULT 'user',  -- user | budget | system
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS verdicts (
    run_id   TEXT PRIMARY KEY,
    verdict  TEXT,                     -- solved|substantial|marginal|nothing|wrong
    comment  TEXT,
    ts       TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_problem  ON runs(problem, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_started  ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_bench    ON runs(bench_id);
CREATE INDEX IF NOT EXISTS idx_usage_ts      ON usage_samples(ts);
CREATE INDEX IF NOT EXISTS idx_usage_run     ON usage_samples(run_id);
CREATE INDEX IF NOT EXISTS idx_usage_account ON usage_samples(account, ts);
CREATE INDEX IF NOT EXISTS idx_acctev        ON account_events(account, ts DESC);
CREATE INDEX IF NOT EXISTS idx_steering_run  ON steering(run_id, ts);
"""


class Database:
    """Thin, thread-safe wrapper around a single SQLite file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- connection handling ------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction. Tolerant of statements that commit implicitly
        (executescript, DDL), which would otherwise leave nothing to COMMIT."""
        conn = self._conn()
        conn.execute("BEGIN")
        try:
            yield conn
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        else:
            if conn.in_transaction:
                conn.execute("COMMIT")

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, tuple(params)).fetchone()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        self._conn().execute(sql, tuple(params))

    # -- typed helpers ------------------------------------------------------

    def upsert_run(self, data: dict[str, Any]) -> None:
        cols = [c for c in data if c != "id"]
        placeholders = ", ".join("?" for _ in data)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols)
        sql = (
            f"INSERT INTO runs ({', '.join(data)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        self.execute(sql, list(data.values()))

    def add_usage(self, **kw: Any) -> None:
        cols = ", ".join(kw)
        placeholders = ", ".join("?" for _ in kw)
        self.execute(f"INSERT INTO usage_samples ({cols}) VALUES ({placeholders})",
                     list(kw.values()))

    def add_account_event(self, account: str, ts: str, kind: str, payload: Any = None) -> None:
        self.execute(
            "INSERT INTO account_events (account, ts, kind, payload) VALUES (?, ?, ?, ?)",
            (account, ts, kind, json.dumps(payload) if payload is not None else None),
        )

    def add_steering(self, run_id: str, ts: str, text: str, origin: str = "user") -> int:
        cur = self._conn().execute(
            "INSERT INTO steering (run_id, ts, text, origin) VALUES (?, ?, ?, ?)",
            (run_id, ts, text, origin),
        )
        return int(cur.lastrowid or 0)

    def mark_steering_delivered(self, steering_id: int, ts: str) -> None:
        self.execute("UPDATE steering SET delivered_at = ? WHERE id = ?", (ts, steering_id))

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


_db: Database | None = None


def get_db(path: Path | None = None) -> Database:
    global _db
    if _db is None:
        if path is None:
            from .config import get_config

            path = get_config().db_path
        _db = Database(path)
    return _db


def reset_db() -> None:
    """Test hook: drop the process-wide handle."""
    global _db
    if _db is not None:
        _db.close()
    _db = None
