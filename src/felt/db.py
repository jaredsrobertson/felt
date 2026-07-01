"""Storage. SQLite by default so the project runs with zero setup.

To move to Supabase/Postgres: keep the same SQL (it's vanilla), swap sqlite3 for
psycopg, and point at the Supabase connection string. The ledger/player code below
only uses standard SQL, so the swap is mechanical.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id   TEXT PRIMARY KEY,   -- telegram user id (as text)
    handle      TEXT                -- telegram @handle, for venmo note matching
);

CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id  TEXT NOT NULL,
    delta      INTEGER NOT NULL,    -- +credit / -debit, in points
    kind       TEXT NOT NULL,       -- 'deposit' | 'bet' | 'settle' | 'payout'
    ref        TEXT,                -- idempotency key (e.g. venmo message-id)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- One ledger row per external event. Dedupes replayed Gmail polls.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_kind_ref
    ON ledger(kind, ref) WHERE ref IS NOT NULL;
"""


def connect(db_path: str | Path = "felt.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
