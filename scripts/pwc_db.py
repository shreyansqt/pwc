"""SQLite connection + schema bootstrap + row helpers.

One short-lived connection per taskdb.py invocation: open, one transaction,
commit, close. WAL mode + busy_timeout let the coordinator (reader) and workers
(append-only writers) operate concurrently without "database is locked" errors.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from _common import db_path, workspace_name, workspaces_below

_SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"
_BUSY_TIMEOUT_MS = 5000


def _no_db_message(path: Path) -> str:
    """Say what is actually wrong, and never advise creating a database we'd ignore.

    Two ways to land here, and the old message got both wrong by telling you to
    `init` a local taskdb.db:
      - You're in a PARENT of workspaces (~/work). Nothing is broken; you just
        didn't say which board you meant. Name them.
      - You're in a hub-backed workspace whose store.json moved the database to
        the cloud. There IS no local taskdb.db and `init` would create a stray one
        that PWC never reads.
    """
    root = path.parent.parent
    below = workspaces_below(root)
    if below:
        names = "\n".join(f"  pwc --workspace {r}   ({workspace_name(r)})"
                          for r in below)
        return (f"{root} is not a workspace, but it CONTAINS these — name one, or "
                f"run a read op here to see them merged:\n{names}")
    store = root / ".pwc" / "store.json"
    if store.exists():
        return (f"no local task database at {path}, but {store} exists — this "
                f"workspace's store is configured elsewhere (e.g. a hub). Do NOT "
                f"`init` a local one; fix or remove store.json instead.")
    return (f"no task database at {path} — run `pwc init` in this workspace first")


def connect(workspace=None, *, must_exist: bool = True) -> sqlite3.Connection:
    """Open the workspace task database. Raises if missing unless `must_exist=False`."""
    path = db_path(workspace)
    if must_exist and not path.exists():
        raise FileNotFoundError(_no_db_message(path))
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
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
    return {"db": str(path), "created": not existed}


def _migrate(conn) -> None:
    """Idempotent column adds for DBs created before a column existed.
    `CREATE TABLE IF NOT EXISTS` in schema.sql is a no-op once the table exists,
    so new columns must be ALTERed in here. Safe to run on every init()."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "archived_at" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN archived_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_archived ON tasks(archived_at)"
    )
    for col in ("harness", "model", "runhost"):
        if col not in cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
    # task_usage and task_sessions are created by schema.sql on every init (CREATE
    # TABLE IF NOT EXISTS), so a DB that predates them picks them up there — nothing
    # to ALTER. After creating task_sessions, backfill it from the append-only
    # `dispatched` events: existing DBs have dispatch history that tasks.session_id
    # never kept (the old sweep NULLed it on worker death; a re-dispatch overwrote
    # it). Run `pwc backfill-sessions` for that — it is idempotent and explicit rather
    # than a silent side effect of init.


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
