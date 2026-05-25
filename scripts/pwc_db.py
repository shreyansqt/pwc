"""SQLite connection + schema bootstrap + row helpers.

One short-lived connection per ledger.py invocation: open, one transaction,
commit, close. WAL mode + busy_timeout let the coordinator (reader) and workers
(append-only writers) operate concurrently without "database is locked" errors.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from _common import db_path

_SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"
_BUSY_TIMEOUT_MS = 5000


def connect(workspace=None, *, must_exist: bool = True) -> sqlite3.Connection:
    """Open the workspace ledger. Raises if missing unless `must_exist=False`."""
    path = db_path(workspace)
    if must_exist and not path.exists():
        raise FileNotFoundError(
            f"no ledger at {path} — run `ledger.py init` in this workspace first"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(workspace=None) -> dict:
    """Create .pwc/ and apply schema.sql. Idempotent."""
    path = db_path(workspace)
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(workspace, must_exist=False)
    try:
        conn.executescript(_SCHEMA.read_text())
        conn.commit()
    finally:
        conn.close()
    return {"db": str(path), "created": not existed}


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
