#!/usr/bin/env python3
"""PWC ledger CLI — the single read/write path to the SQLite ledger.

All output is one JSON value on stdout; diagnostics go to stderr; exit 1 on error.
Writes are single transactions; multi-tier writes (e.g. set-session + dispatched
event) commit atomically. Workers use only `log-event`; the coordinator owns task
mutations.

Usage:
  ledger.py <subcommand> [flags]   [--workspace PATH]

See `ledger.py --help` or each subcommand's flags below.
"""

from __future__ import annotations

import argparse
import sys

import pwc_db
from _common import days_ago_iso, emit, fail, now_iso

# Columns surfaced in the always-loaded summary (the index tier). Deliberately
# small — one status line's worth per task.
_SUMMARY_COLS = (
    "id, type, title, status, priority, parked, parked_reason, "
    "session_id, last_event_at"
)


# ── id assignment ───────────────────────────────────────────────────────────
def _next_task_id(conn) -> str:
    """Sequential stable id like t_0007. Stable across archives (max+1 over all)."""
    row = conn.execute(
        "SELECT id FROM tasks WHERE id LIKE 't\\_%' ESCAPE '\\' "
        "ORDER BY CAST(SUBSTR(id, 3) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    n = (int(row["id"][2:]) + 1) if row else 1
    return f"t_{n:04d}"


def _touch_last_event(conn, task_id: str, at: str) -> None:
    """Maintain the denormalized last_event_at cache used by the staleness sweep."""
    if task_id is not None:
        conn.execute(
            "UPDATE tasks SET last_event_at = ? WHERE id = ?", (at, task_id)
        )


def _insert_event(conn, *, task_id, source, kind, detail, at=None) -> int:
    at = at or now_iso()
    cur = conn.execute(
        "INSERT INTO events (task_id, at, source, kind, detail) VALUES (?,?,?,?,?)",
        (task_id, at, source, kind, detail),
    )
    _touch_last_event(conn, task_id, at)
    return cur.lastrowid


def _require_task(conn, task_id: str):
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        fail(f"no task {task_id!r}")
    return row


# ── reads ────────────────────────────────────────────────────────────────────
def cmd_init(args):
    emit(pwc_db.init(args.workspace))


def cmd_summary(args):
    conn = pwc_db.connect(args.workspace)
    where = "" if args.include_archived else "WHERE archived_at IS NULL"
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS} FROM tasks {where} "
        "ORDER BY (priority IS NULL), priority, last_event_at DESC"
    ).fetchall()
    emit(pwc_db.rows_to_dicts(rows))


def cmd_detail(args):
    conn = pwc_db.connect(args.workspace)
    task = pwc_db.row_to_dict(_require_task(conn, args.task))
    refs = conn.execute(
        "SELECT kind, ref_type, value, label, created_at FROM task_refs "
        "WHERE task_id = ? ORDER BY id",
        (args.task,),
    ).fetchall()
    events = conn.execute(
        "SELECT at, source, kind, detail FROM events "
        "WHERE task_id = ? ORDER BY at, id",
        (args.task,),
    ).fetchall()
    emit({
        "task": task,
        "refs": pwc_db.rows_to_dicts(refs),
        "events": pwc_db.rows_to_dicts(events),
    })


def cmd_stale(args):
    """Active, not-parked tasks untouched beyond the threshold. Surfaced, not acted on."""
    conn = pwc_db.connect(args.workspace)
    cutoff = days_ago_iso(args.threshold_days)
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS} FROM tasks "
        "WHERE archived_at IS NULL AND parked = 0 "
        "  AND COALESCE(last_event_at, created_at) < ? "
        "ORDER BY COALESCE(last_event_at, created_at)",
        (cutoff,),
    ).fetchall()
    emit(pwc_db.rows_to_dicts(rows))


def cmd_parked_aging(args):
    """Parked tasks aged beyond the threshold — the gentler 'still waiting?' nudge."""
    conn = pwc_db.connect(args.workspace)
    cutoff = days_ago_iso(args.threshold_days)
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS} FROM tasks "
        "WHERE archived_at IS NULL AND parked = 1 "
        "  AND COALESCE(last_event_at, created_at) < ? "
        "ORDER BY COALESCE(last_event_at, created_at)",
        (cutoff,),
    ).fetchall()
    emit(pwc_db.rows_to_dicts(rows))


def cmd_events(args):
    conn = pwc_db.connect(args.workspace)
    clauses, params = [], []
    if args.task:
        clauses.append("task_id = ?")
        params.append(args.task)
    if args.since:
        clauses.append("at >= ?")
        params.append(args.since)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT id, task_id, at, source, kind, detail FROM events {where} "
        "ORDER BY at, id",
        params,
    ).fetchall()
    emit(pwc_db.rows_to_dicts(rows))


def cmd_find_refs(args):
    """Tasks carrying a ref matching (ref_type, value). The inbound-matcher query path."""
    conn = pwc_db.connect(args.workspace)
    clauses, params = ["value = ?"], [args.value]
    if args.ref_type:
        clauses.append("ref_type = ?")
        params.append(args.ref_type)
    if args.kind:
        clauses.append("kind = ?")
        params.append(args.kind)
    rows = conn.execute(
        "SELECT DISTINCT t.id, t.type, t.title, t.status, t.archived_at "
        "FROM task_refs r JOIN tasks t ON t.id = r.task_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY t.id",
        params,
    ).fetchall()
    emit(pwc_db.rows_to_dicts(rows))


# ── writes ─────────────────────────────────────────────────────────────────--
def cmd_add_task(args):
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _next_task_id(conn)
        ts = now_iso()
        conn.execute(
            "INSERT INTO tasks (id, type, title, status, priority, notes, "
            "  parked, parked_reason, workdir, inline, created_at, updated_at, last_event_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, args.type, args.title, args.status, args.priority, args.notes,
             1 if args.parked else 0, args.parked_reason, args.workdir,
             1 if args.inline else 0, ts, ts, ts),
        )
        _insert_event(conn, task_id=tid, source="coordinator", kind="created",
                      detail=args.title, at=ts)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_update_task(args):
    conn = pwc_db.connect(args.workspace)
    with conn:
        old = _require_task(conn, args.task)
        sets, params, changes = [], [], []
        for field in ("status", "priority", "notes", "parked_reason", "workdir"):
            val = getattr(args, field)
            if val is not None:
                sets.append(f"{field} = ?")
                params.append(val)
                if old[field] != val:
                    changes.append(f"{field}: {old[field]!r} -> {val!r}")
        if args.parked is not None:
            sets.append("parked = ?")
            params.append(1 if args.parked else 0)
        if not sets:
            fail("update-task: nothing to change")
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(args.task)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        # Log a status event when status changed, else a generic note of the change.
        if args.status is not None and old["status"] != args.status:
            _insert_event(conn, task_id=args.task, source="coordinator",
                          kind="status", detail=f"status -> {args.status}")
        elif changes:
            _insert_event(conn, task_id=args.task, source="coordinator",
                          kind="note", detail="; ".join(changes))
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.task,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_add_ref(args):
    conn = pwc_db.connect(args.workspace)
    with conn:
        _require_task(conn, args.task)
        conn.execute(
            "INSERT INTO task_refs (task_id, kind, ref_type, value, label, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (args.task, args.kind, args.ref_type, args.value, args.label, now_iso()),
        )
        row = conn.execute(
            "SELECT * FROM task_refs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (args.task,),
        ).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_log_event(args):
    """The single write path workers use. Append-only into events (+ last_event_at)."""
    conn = pwc_db.connect(args.workspace)
    with conn:
        if args.task:
            _require_task(conn, args.task)
        eid = _insert_event(conn, task_id=args.task, source=args.source,
                            kind=args.kind, detail=args.detail)
        row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_set_session(args):
    """Record the pre-allocated worker session id at spawn, atomic with a dispatched event."""
    conn = pwc_db.connect(args.workspace)
    with conn:
        _require_task(conn, args.task)
        sets = ["session_id = ?", "updated_at = ?"]
        params = [args.session_id, now_iso()]
        if args.workdir is not None:
            sets.append("workdir = ?")
            params.append(args.workdir)
        params.append(args.task)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        _insert_event(conn, task_id=args.task, source="coordinator",
                      kind="dispatched", detail=f"session {args.session_id}")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.task,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_archive(args):
    conn = pwc_db.connect(args.workspace)
    with conn:
        _require_task(conn, args.task)
        ts = now_iso()
        conn.execute(
            "UPDATE tasks SET archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, args.task),
        )
        _insert_event(conn, task_id=args.task, source="coordinator",
                      kind="rollup", detail="archived", at=ts)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.task,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_set_status_gone(args):
    """Liveness convenience: mark a vanished worker's task 'gone — needs triage'."""
    conn = pwc_db.connect(args.workspace)
    with conn:
        _require_task(conn, args.task)
        conn.execute(
            "UPDATE tasks SET status = 'gone', updated_at = ? WHERE id = ?",
            (now_iso(), args.task),
        )
        _insert_event(conn, task_id=args.task, source="brief", kind="gone",
                      detail="worker session no longer running — needs triage")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.task,)).fetchone()
    emit(pwc_db.row_to_dict(row))


# ── arg parsing ───────────────────────────────────────────────────────────---
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledger.py", description=__doc__)
    p.add_argument("--workspace", help="workspace root (default: discover from cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    s = sub.add_parser("summary")
    s.add_argument("--include-archived", action="store_true")
    s.set_defaults(func=cmd_summary)

    s = sub.add_parser("detail")
    s.add_argument("--task", required=True)
    s.set_defaults(func=cmd_detail)

    s = sub.add_parser("stale")
    s.add_argument("--threshold-days", type=float, default=7.0)
    s.set_defaults(func=cmd_stale)

    s = sub.add_parser("parked-aging")
    s.add_argument("--threshold-days", type=float, default=14.0)
    s.set_defaults(func=cmd_parked_aging)

    s = sub.add_parser("events")
    s.add_argument("--task")
    s.add_argument("--since", help="ISO8601; events at or after this time")
    s.set_defaults(func=cmd_events)

    s = sub.add_parser("find-refs")
    s.add_argument("--value", required=True)
    s.add_argument("--ref-type")
    s.add_argument("--kind", choices=("identity", "working"))
    s.set_defaults(func=cmd_find_refs)

    s = sub.add_parser("add-task")
    s.add_argument("--type", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--status", default="active")
    s.add_argument("--priority", type=int)
    s.add_argument("--notes")
    s.add_argument("--workdir")
    s.add_argument("--parked", action="store_true")
    s.add_argument("--parked-reason")
    s.add_argument("--inline", action="store_true")
    s.set_defaults(func=cmd_add_task)

    s = sub.add_parser("update-task")
    s.add_argument("--task", required=True)
    s.add_argument("--status")
    s.add_argument("--priority", type=int)
    s.add_argument("--notes")
    s.add_argument("--workdir")
    s.add_argument("--parked-reason")
    s.add_argument("--parked", type=int, choices=(0, 1))
    s.set_defaults(func=cmd_update_task)

    s = sub.add_parser("add-ref")
    s.add_argument("--task", required=True)
    s.add_argument("--kind", required=True, choices=("identity", "working"))
    s.add_argument("--ref-type", required=True)
    s.add_argument("--value", required=True)
    s.add_argument("--label")
    s.set_defaults(func=cmd_add_ref)

    s = sub.add_parser("log-event")
    s.add_argument("--task")
    s.add_argument("--source", default="coordinator",
                   choices=("coordinator", "worker", "brief", "system"))
    s.add_argument("--kind", required=True)
    s.add_argument("--detail")
    s.set_defaults(func=cmd_log_event)

    s = sub.add_parser("set-session")
    s.add_argument("--task", required=True)
    s.add_argument("--session-id", required=True)
    s.add_argument("--workdir")
    s.set_defaults(func=cmd_set_session)

    s = sub.add_parser("archive")
    s.add_argument("--task", required=True)
    s.set_defaults(func=cmd_archive)

    s = sub.add_parser("set-status-gone")
    s.add_argument("--task", required=True)
    s.set_defaults(func=cmd_set_status_gone)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except FileNotFoundError as e:
        fail(str(e))
    except Exception as e:  # noqa: BLE001 — surface any error as a clean diagnostic
        fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
