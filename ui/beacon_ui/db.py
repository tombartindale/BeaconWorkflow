"""The UI's own small SQLite file: job history and preferences, nothing else.

Pipeline state never goes in here. Deleting this file loses job history and
preferences; every topic still looks exactly as done as it is.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    started TEXT,
    finished TEXT,
    by TEXT,
    command TEXT NOT NULL,
    label TEXT NOT NULL,
    targets TEXT NOT NULL,      -- JSON list of paths relative to the root
    args TEXT NOT NULL,         -- JSON object
    state TEXT NOT NULL,        -- queued | running | done | failed | cancelled | interrupted
    exit_code INTEGER,
    duration_ms INTEGER,
    envelope TEXT,              -- JSON: the envelope(s) exactly as bcn printed them
    log TEXT                    -- the tail of stderr, NDJSON lines as received
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_PREFS: dict[str, Any] = {
    "operator": "",
    "jobs": 1,                 # passed through to bcn --jobs
    "parallel_jobs": 2,        # queue jobs running at once (never two on the same topic)
    "theme": "",               # empty: let bcn resolve it
    "poll_seconds": 15,
    "module_columns": {"en": True, "zh": True},
    "topic_layout": "side",    # side | stacked
}


class DB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # -- prefs --------------------------------------------------------------------
    def prefs(self) -> dict[str, Any]:
        out = dict(DEFAULT_PREFS)
        for row in self.query("SELECT key, value FROM prefs"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except ValueError:
                continue
        return out

    def set_prefs(self, values: dict[str, Any]) -> dict[str, Any]:
        for k, v in values.items():
            if k in DEFAULT_PREFS:
                self.execute("INSERT INTO prefs(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (k, json.dumps(v)))
        return self.prefs()
