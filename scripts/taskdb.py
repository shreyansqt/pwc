#!/usr/bin/env python3
"""PWC task database CLI — the single read/write path to the SQLite task DB.

All output is one JSON value on stdout; diagnostics go to stderr; exit 1 on error.
Writes are single transactions; multi-tier writes (e.g. set-session + dispatched
event) commit atomically. Workers use only `log-event`; the coordinator owns task
mutations.

Usage:
  taskdb.py <subcommand> [flags]   [--workspace PATH]

See `taskdb.py --help` or each subcommand's flags below.
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


# ── ids: slugify, dedup, resolve-through-aliases ──────────────────────────────
import re as _re


def slugify(text: str, maxwords: int = 4) -> str:
    """Lowercase, alphanumeric-and-hyphen slug from free text (first few words)."""
    words = _re.findall(r"[a-z0-9]+", (text or "").lower())
    slug = "-".join(words[:maxwords])
    return slug or "task"


def _dedup_id(conn, base: str) -> str:
    """Return `base`, or base-2/-3/... if it (or an alias) is already taken."""
    candidate, n = base, 1
    while conn.execute(
        "SELECT 1 FROM tasks WHERE id = ? "
        "UNION SELECT 1 FROM task_aliases WHERE alias = ?",
        (candidate, candidate),
    ).fetchone():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _resolve_id(conn, task_id: str) -> str | None:
    """Map any id or former alias to the current canonical tasks.id (or None)."""
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
        return task_id
    row = conn.execute(
        "SELECT task_id FROM task_aliases WHERE alias = ?", (task_id,)
    ).fetchone()
    return row["task_id"] if row else None


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
    """Fetch a task by id OR a former alias. Returns the canonical row."""
    canonical = _resolve_id(conn, task_id)
    if canonical is None:
        fail(f"no task {task_id!r}")
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (canonical,)).fetchone()


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
    row = _require_task(conn, args.task)
    tid = row["id"]  # canonical, in case args.task was an alias
    task = pwc_db.row_to_dict(row)
    refs = conn.execute(
        "SELECT kind, ref_type, value, label, created_at FROM task_refs "
        "WHERE task_id = ? ORDER BY id",
        (tid,),
    ).fetchall()
    events = conn.execute(
        "SELECT at, source, kind, detail FROM events "
        "WHERE task_id = ? ORDER BY at, id",
        (tid,),
    ).fetchall()
    aliases = conn.execute(
        "SELECT alias FROM task_aliases WHERE task_id = ? ORDER BY created_at", (tid,)
    ).fetchall()
    emit({
        "task": task,
        "refs": pwc_db.rows_to_dicts(refs),
        "events": pwc_db.rows_to_dicts(events),
        "aliases": [a["alias"] for a in aliases],
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
        # Id is meaningful: caller passes --task (a Jira key, or a <source>-<slug>
        # the skill built from the conventions). If omitted, fall back to a slug of
        # the title. Either way, dedup against existing ids and aliases.
        base = args.task or slugify(args.title)
        tid = _dedup_id(conn, base)
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
        tid = old["id"]  # canonical
        sets, params, changes = [], [], []
        for field in ("title", "status", "priority", "notes", "parked_reason", "workdir"):
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
        params.append(tid)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        # Log a status event when status changed, else a generic note of the change.
        if args.status is not None and old["status"] != args.status:
            _insert_event(conn, task_id=tid, source="coordinator",
                          kind="status", detail=f"status -> {args.status}")
        elif changes:
            _insert_event(conn, task_id=tid, source="coordinator",
                          kind="note", detail="; ".join(changes))
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_add_ref(args):
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"]
        conn.execute(
            "INSERT INTO task_refs (task_id, kind, ref_type, value, label, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (tid, args.kind, args.ref_type, args.value, args.label, now_iso()),
        )
        row = conn.execute(
            "SELECT * FROM task_refs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_log_event(args):
    """The single write path workers use. Append-only into events (+ last_event_at)."""
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"] if args.task else None
        eid = _insert_event(conn, task_id=tid, source=args.source,
                            kind=args.kind, detail=args.detail)
        row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_set_session(args):
    """Record the pre-allocated worker session id at spawn, atomic with a dispatched event."""
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"]
        sets = ["session_id = ?", "updated_at = ?"]
        params = [args.session_id, now_iso()]
        if args.workdir is not None:
            sets.append("workdir = ?")
            params.append(args.workdir)
        params.append(tid)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        _insert_event(conn, task_id=tid, source="coordinator",
                      kind="dispatched", detail=f"session {args.session_id}")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_archive(args):
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"]
        ts = now_iso()
        conn.execute(
            "UPDATE tasks SET archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, tid),
        )
        _insert_event(conn, task_id=tid, source="coordinator",
                      kind="archived", detail="archived", at=ts)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_set_status_gone(args):
    """Liveness convenience: mark a vanished worker's task 'gone — needs triage'."""
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"]
        conn.execute(
            "UPDATE tasks SET status = 'gone', updated_at = ? WHERE id = ?",
            (now_iso(), tid),
        )
        _insert_event(conn, task_id=tid, source="brief", kind="gone",
                      detail="worker session no longer running — needs triage")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_promote(args):
    """Give a task a new canonical id (e.g. a Jira key it just gained), keeping its
    old id as an alias so prior references still resolve. Re-points the task row,
    its refs, events, and any existing aliases to the new id."""
    conn = pwc_db.connect(args.workspace)
    with conn:
        row = _require_task(conn, args.task)
        old_id, new_id = row["id"], args.new_id
        if new_id == old_id:
            fail(f"task is already {new_id!r}")
        if _resolve_id(conn, new_id) is not None:
            fail(f"id {new_id!r} is already taken")
        ts = now_iso()
        # Re-key the row and everything that references it. FKs are deferred within
        # the transaction; insert the new tasks row, repoint children, drop the old.
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("UPDATE tasks SET id = ?, updated_at = ? WHERE id = ?",
                     (new_id, ts, old_id))
        conn.execute("UPDATE task_refs SET task_id = ? WHERE task_id = ?",
                     (new_id, old_id))
        conn.execute("UPDATE events SET task_id = ? WHERE task_id = ?",
                     (new_id, old_id))
        conn.execute("UPDATE task_aliases SET task_id = ? WHERE task_id = ?",
                     (new_id, old_id))
        # Record the old id as an alias so it keeps resolving.
        conn.execute(
            "INSERT INTO task_aliases (alias, task_id, created_at) VALUES (?,?,?)",
            (old_id, new_id, ts),
        )
        _insert_event(conn, task_id=new_id, source="coordinator", kind="note",
                      detail=f"promoted: id {old_id} -> {new_id} (old id kept as alias)")
        out = conn.execute("SELECT * FROM tasks WHERE id = ?", (new_id,)).fetchone()
    emit(pwc_db.row_to_dict(out))


# ── arg parsing ───────────────────────────────────────────────────────────---
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="taskdb.py", description=__doc__)
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
    s.add_argument("--task", help="meaningful id (Jira key or <source>-<slug>); "
                                  "deduped if taken. Defaults to a slug of the title.")
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
    s.add_argument("--title")
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

    s = sub.add_parser("promote")
    s.add_argument("--task", required=True, help="current id or alias")
    s.add_argument("--new-id", required=True, help="new canonical id, e.g. a Jira key")
    s.set_defaults(func=cmd_promote)

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
