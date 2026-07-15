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
from _common import (READ_OPS, days_ago_iso, emit, fail, multi_workspace_fail,
                     now_iso, read_json_stdin, resolve_task_workspace,
                     store_config, workspace_name, workspaces_below)

# Columns surfaced in the always-loaded summary (the index tier). Deliberately
# small — one status line's worth per task.
_SUMMARY_COLS = (
    "id, type, title, status, priority, parked, parked_reason, "
    "archived_at, workdir, session_id, harness, model, runhost, last_event_at"
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
    """The board. By default: every not-done, not-archived task, plus done tasks
    closed within the recent window (`--done-within-days`, default 2) so the board
    doubles as a short 'what just finished' timeline. Older done tasks age off on
    their own. `--all` shows every NON-archived task ever, regardless of age.
    `--archived` shows ONLY archived tasks (the off-board set) — archived tasks never
    appear in the default or `--all` board."""
    conn = pwc_db.connect(args.workspace)
    params = []
    if args.archived:
        # The off-board set: only archived tasks, whatever their status.
        where = "WHERE archived_at IS NOT NULL"
    elif args.all:
        # Every task ever, except archived ones.
        where = "WHERE archived_at IS NULL"
    else:
        # not-done tasks always show; done tasks only while still in the window.
        # archived tasks are excluded regardless of status.
        cutoff = days_ago_iso(args.done_within_days)
        where = ("WHERE archived_at IS NULL "
                 "AND (status != 'done' OR COALESCE(updated_at, created_at) >= ?)")
        params.append(cutoff)
    rows = conn.execute(
        f"SELECT {_SUMMARY_COLS} FROM tasks {where} "
        "ORDER BY (priority IS NULL), priority, last_event_at DESC",
        params,
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
        "WHERE status != 'done' AND parked = 0 AND archived_at IS NULL "
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
        "WHERE status != 'done' AND parked = 1 AND archived_at IS NULL "
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


def cmd_find_session(args):
    """The task currently holding this worker session id (or null).

    Reverse of `set-session`: maps a `claude --session-id <uuid>` back to its task,
    so a worker that knows only its own session id can find its task. Returns the
    same summary shape as `summary`, or `null` if no task carries that session.
    """
    conn = pwc_db.connect(args.workspace)
    row = conn.execute(
        f"SELECT {_SUMMARY_COLS} FROM tasks "
        "WHERE session_id = ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (args.session_id,),
    ).fetchone()
    emit(pwc_db.row_to_dict(row))


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
        "SELECT DISTINCT t.id, t.type, t.title, t.status "
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
            "  parked, parked_reason, workdir, harness, model, runhost, inline, "
            "  created_at, updated_at, last_event_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, args.type, args.title, args.status, args.priority, args.notes,
             1 if args.parked else 0, args.parked_reason, args.workdir,
             args.harness, args.model, args.runhost,
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
        for field in ("title", "status", "priority", "notes", "parked_reason",
                      "workdir", "harness", "model", "runhost"):
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
    """The single write path workers use. Append-only into events (+ last_event_at).

    With `--set-status`, the same transaction also updates the task's status field —
    so a worker reporting `blocked`/`awaiting-review`/`done` moves the task there
    rather than just logging an event that `show-work` then shows out of sync. Emits
    the task row when status was set (so the caller sees the new status), else the
    event row. Requires `--task` when `--set-status` is given.
    """
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"] if args.task else None
        if args.set_status and tid is None:
            fail("log-event --set-status requires --task")
        eid = _insert_event(conn, task_id=tid, source=args.source,
                            kind=args.kind, detail=args.detail)
        if args.set_status and tid is not None:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (args.set_status, now_iso(), tid),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_set_session(args):
    """Record the worker session at spawn. Appends to task_sessions AND points
    tasks.session_id at it, atomically with a `dispatched` event.

    task_sessions is the durable provenance (every session that ever ran this task);
    tasks.session_id is just "the one to resume next". A RESUME of an existing session
    hits the same task_sessions row (PK is task+session) and refreshes started_at —
    it is the same session, reopened, not a new one.
    """
    conn = pwc_db.connect(args.workspace)
    with conn:
        task = _require_task(conn, args.task)
        tid = task["id"]
        ts = now_iso()
        sets = ["session_id = ?", "updated_at = ?"]
        params = [args.session_id, ts]
        if args.workdir is not None:
            sets.append("workdir = ?")
            params.append(args.workdir)
        params.append(tid)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        # The permanent record. Harness/model default to whatever the task carries,
        # so a session is always attributable to what actually ran it.
        conn.execute(
            "INSERT INTO task_sessions (task_id, session_id, harness, model, started_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(task_id, session_id) DO UPDATE SET started_at = excluded.started_at",
            (tid, args.session_id, args.harness or task["harness"],
             args.model or task["model"], ts),
        )
        _insert_event(conn, task_id=tid, source="coordinator",
                      kind="dispatched", detail=f"session {args.session_id}")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


_DISPATCH_SESSION_RE = _re.compile(
    r"\b(?:session\s+)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|ses_[A-Za-z0-9]+)\b")


def cmd_backfill_sessions(args):
    """Rebuild task_sessions from the `dispatched` event log.

    Every spawn wrote an append-only `dispatched` event carrying its session id, and
    events are never mutated — so the full dispatch history survived even where
    `tasks.session_id` did not (the old sweep NULLed it whenever a worker died, and a
    re-dispatch silently overwrote it). That makes the event log the ONLY complete
    record of what ran what, and this the one-time repair.

    Idempotent: re-running just re-upserts the same rows.
    """
    conn = pwc_db.connect(args.workspace)
    with conn:
        rows = conn.execute(
            "SELECT task_id, at, detail FROM events "
            "WHERE kind = 'dispatched' AND task_id IS NOT NULL ORDER BY at, id"
        ).fetchall()
        seen, restored = set(), 0
        for r in rows:
            m = _DISPATCH_SESSION_RE.search(r["detail"] or "")
            if not m:
                continue
            sid = m.group(1)
            key = (r["task_id"], sid)
            task = conn.execute(
                "SELECT harness, model FROM tasks WHERE id = ?", (r["task_id"],)
            ).fetchone()
            if task is None:  # task was merged/deleted; its events may still exist
                continue
            conn.execute(
                "INSERT INTO task_sessions (task_id, session_id, harness, model, started_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(task_id, session_id) DO UPDATE SET "
                "  started_at = MIN(task_sessions.started_at, excluded.started_at)",
                (r["task_id"], sid, task["harness"], task["model"], r["at"]),
            )
            if key not in seen:
                seen.add(key)
                restored += 1
        multi = conn.execute(
            "SELECT COUNT(*) n FROM (SELECT task_id FROM task_sessions "
            "GROUP BY task_id HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
    emit({
        "dispatch_events_scanned": len(rows),
        "sessions_restored": restored,
        "tasks_with_multiple_sessions": multi,
        "note": "rebuilt from the append-only `dispatched` events — the only record "
                "that survived the old clear-session sweep and re-dispatch overwrites",
    })


def cmd_sessions(args):
    """Every session that has ever run this task, oldest first.

    The provenance record `tasks.session_id` cannot hold (it has one slot and silently
    overwrites). This is what `pwc cost` sums over, and what tells you a task was
    retried, resumed, or re-run on a different model.
    """
    conn = pwc_db.connect(args.workspace)
    tid = _require_task(conn, args.task)["id"]
    rows = conn.execute(
        "SELECT session_id, harness, model, started_at FROM task_sessions "
        "WHERE task_id = ? ORDER BY started_at, session_id",
        (tid,),
    ).fetchall()
    emit(pwc_db.rows_to_dicts(rows))


def cmd_clear_session(args):
    """Detach a task's worker session: set session_id back to NULL. DESTRUCTIVE.

    The inverse of `set-session`. Use ONLY when a session id was recorded by MISTAKE
    (wrong id, wrong task) and should genuinely be forgotten.

    Do NOT use it to mean "the worker isn't running anymore." A dead worker PROCESS is
    not a gone SESSION: the harness transcript persists on disk and stays resumable
    (`claude --resume <uuid>`) indefinitely, and the session id is the ONLY handle on
    it. Clearing it silently costs you three things — resume (`/pwc-start-work` reopens
    the prior session instead of starting cold, so a task picked up days later keeps
    its context), cost measurement (`pwc cost` finds the transcript by this id), and
    the provenance record of what actually did the work.

    Liveness is COMPUTED, not stored: `pwc worker-status` runs pgrep and answers
    "is a worker running?" on demand. So nulling this field to encode "not running"
    trades a durable fact for a transient one you can re-derive in milliseconds.
    (This was a real bug: /pwc-show-work's sweep used to clear the session of every
    dead worker — i.e. exactly when a task FINISHED — so tasks became unresumable and
    unpriceable at the moment their history mattered most.)

    Logs a neutral note (NOT a `dispatched` event — clearing is not a dispatch) and
    leaves status untouched; change status separately with `update-task` if needed.
    """
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"]
        conn.execute(
            "UPDATE tasks SET session_id = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), tid),
        )
        _insert_event(conn, task_id=tid, source="coordinator", kind="note",
                      detail="session cleared (detached worker session)")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


def cmd_reroute(args):
    """Clear a task's stored harness/model so the next spawn forces a fresh route.

    For re-scoped tasks whose routing is stale (a task picked up for a different
    chapter than it was last dispatched for). This does NOT clear the session —
    those concerns are separate and explicit. After reroute, `pwc spawn` will refuse
    with a route template, and the coordinator runs `pwc route` to re-profile it.
    """
    conn = pwc_db.connect(args.workspace)
    with conn:
        row = _require_task(conn, args.task)
        tid = row["id"]
        ts = now_iso()
        reason = args.reason or "task re-scoped; routing invalidated"
        conn.execute(
            "UPDATE tasks SET harness = NULL, model = NULL, updated_at = ? WHERE id = ?",
            (ts, tid),
        )
        _insert_event(conn, task_id=tid, source="coordinator", kind="note",
                      detail=f"routing cleared: {reason}")
        out = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    result = pwc_db.row_to_dict(out)
    # The caller needs a hint because the next spawn WILL refuse.
    result["_hint"] = ("routing cleared — run `pwc route` to re-profile this task, "
                       f"then `pwc update-task --task {tid} --harness <h> --model <m>` "
                       "to store the result")
    emit(result)


def cmd_archive(args):
    """Remove a task from the board WITHOUT marking it done.

    Archive = "off my board, but not completed" — for work that turned out not to be
    mine, got dropped/superseded, or is someone else's ticket I was only tracking.
    It is deliberately NOT the same as status='done': the task's real status is
    preserved (a pending task stays pending, an in-progress one stays in-progress);
    archive just hides it from `summary` and stamps `archived_at` with WHEN it left.
    Archived tasks resurface only via `summary --archived`. `--reason` is required when
    archiving so the board history records WHY it left; `--unarchive` clears
    `archived_at` and puts it back on the board (no reason needed).
    """
    conn = pwc_db.connect(args.workspace)
    with conn:
        tid = _require_task(conn, args.task)["id"]
        if args.unarchive:
            conn.execute(
                "UPDATE tasks SET archived_at = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), tid),
            )
            _insert_event(conn, task_id=tid, source="coordinator", kind="unarchive",
                          detail="unarchived (back on the board)")
        else:
            if not args.reason:
                fail("archive: --reason is required (why is this leaving the board?)")
            ts = now_iso()
            conn.execute(
                "UPDATE tasks SET archived_at = ?, updated_at = ? WHERE id = ?",
                (ts, ts, tid),
            )
            _insert_event(conn, task_id=tid, source="coordinator", kind="archive",
                          detail=f"archived (off board, not done): {args.reason}")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    emit(pwc_db.row_to_dict(row))


_EXPORT_TABLES = ("tasks", "task_aliases", "task_refs", "events", "task_sessions")


def cmd_export(args):
    """Dump the whole workspace as JSON — the migration/backup/exit format.

    Shape: {"tasks": [...], "task_aliases": [...], "task_refs": [...],
    "events": [...]}. The hub's /import accepts exactly this (and its /export
    returns it), so moving a workspace local→hub or hub→local is one pipe.
    """
    conn = pwc_db.connect(args.workspace)
    dump = {}
    for table in _EXPORT_TABLES:
        order = "id" if table in ("task_refs", "events") else "rowid"
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
        dump[table] = pwc_db.rows_to_dicts(rows)
    emit(dump)


def cmd_import(args):
    """Load an export dump into an EMPTY workspace (refuses to merge).

    task_refs/events are re-inserted without their autoincrement ids, in
    original-id order, so timelines stay sorted; everything else keeps its ids.
    """
    if args.json != "-":
        fail("import: only --json - (read from stdin) is supported")
    dump = read_json_stdin()
    missing = [t for t in _EXPORT_TABLES if not isinstance(dump.get(t), list)]
    if missing:
        fail(f"import: dump is missing table(s): {', '.join(missing)}")
    conn = pwc_db.connect(args.workspace)
    with conn:
        if conn.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
            fail("import: this workspace already has tasks — import only into an "
                 "empty task database (init a fresh one, or delete .pwc/taskdb.db)")
        counts = {}
        for table in _EXPORT_TABLES:
            rows = dump[table]
            for row in rows:
                cols = {k: v for k, v in row.items()
                        if not (table in ("task_refs", "events") and k == "id")}
                placeholders = ",".join("?" * len(cols))
                conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                    tuple(cols.values()),
                )
            counts[table] = len(rows)
    emit({"imported": counts})


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


def cmd_merge(args):
    """Merge one task INTO another: `--from` is absorbed into `--into`, which survives.

    The surviving task inherits the absorbed task's identity/working refs (so its
    Jira keys etc. now resolve to the survivor — `find-work` won't re-propose them)
    and its event history. The absorbed id and any aliases it had become aliases of
    the survivor, so `--task <absorbed-id>` keeps resolving. The absorbed row is then
    deleted. This is the real "these two tickets are one piece of work" operation —
    use it instead of faking a combine with a stray extra ref + notes.

    Refs are de-duplicated by (kind, ref_type, value) so a shared ref isn't doubled.
    The survivor's own fields (title, status, priority, ...) are untouched except
    that the absorbed task's notes, if any, are appended to the survivor's notes.
    """
    conn = pwc_db.connect(args.workspace)
    with conn:
        survivor = _require_task(conn, args.into)
        absorbed = _require_task(conn, getattr(args, "from"))
        into_id, from_id = survivor["id"], absorbed["id"]
        if into_id == from_id:
            fail("cannot merge a task into itself")
        ts = now_iso()

        # 1. Move refs, skipping ones the survivor already has (by identity tuple).
        existing = {
            (r["kind"], r["ref_type"], r["value"])
            for r in conn.execute(
                "SELECT kind, ref_type, value FROM task_refs WHERE task_id = ?",
                (into_id,),
            ).fetchall()
        }
        for r in conn.execute(
            "SELECT id, kind, ref_type, value FROM task_refs WHERE task_id = ?",
            (from_id,),
        ).fetchall():
            if (r["kind"], r["ref_type"], r["value"]) in existing:
                conn.execute("DELETE FROM task_refs WHERE id = ?", (r["id"],))
            else:
                conn.execute(
                    "UPDATE task_refs SET task_id = ? WHERE id = ?", (into_id, r["id"])
                )

        # 2. Move event history onto the survivor.
        conn.execute(
            "UPDATE events SET task_id = ? WHERE task_id = ?", (into_id, from_id)
        )

        # 3. Re-point the absorbed task's aliases, then make its id an alias too.
        conn.execute(
            "UPDATE task_aliases SET task_id = ? WHERE task_id = ?", (into_id, from_id)
        )
        conn.execute(
            "INSERT OR IGNORE INTO task_aliases (alias, task_id, created_at) "
            "VALUES (?,?,?)",
            (from_id, into_id, ts),
        )

        # 4. Fold the absorbed notes into the survivor's notes.
        if absorbed["notes"]:
            merged_notes = (
                f"{survivor['notes']}\n\n[merged from {from_id}] {absorbed['notes']}"
                if survivor["notes"]
                else f"[merged from {from_id}] {absorbed['notes']}"
            )
            conn.execute(
                "UPDATE tasks SET notes = ?, updated_at = ? WHERE id = ?",
                (merged_notes, ts, into_id),
            )

        # 5. Delete the absorbed row (its children are already re-pointed).
        conn.execute("DELETE FROM tasks WHERE id = ?", (from_id,))

        _insert_event(
            conn, task_id=into_id, source="coordinator", kind="note",
            detail=f"merged {from_id} into {into_id} (refs, history, aliases absorbed)",
            at=ts,
        )
        out = conn.execute("SELECT * FROM tasks WHERE id = ?", (into_id,)).fetchone()
    emit(pwc_db.row_to_dict(out))


# ── arg parsing ───────────────────────────────────────────────────────────---
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="taskdb.py", description=__doc__)
    p.add_argument("--workspace", help="workspace root (default: discover from cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    s = sub.add_parser("summary")
    s.add_argument("--all", action="store_true",
                   help="show every NON-archived task ever, including done ones older than the window")
    s.add_argument("--archived", action="store_true",
                   help="show ONLY archived (off-board) tasks instead of the board")
    s.add_argument("--done-within-days", type=float, default=2.0,
                   help="how long a done task stays on the board before aging off (default 2)")
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

    s = sub.add_parser("find-session")
    s.add_argument("--session-id", required=True,
                   help="a worker's claude session uuid; returns its task (or null)")
    s.set_defaults(func=cmd_find_session)

    s = sub.add_parser("add-task")
    s.add_argument("--task", help="meaningful id (Jira key or <source>-<slug>); "
                                  "deduped if taken. Defaults to a slug of the title.")
    s.add_argument("--type", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--status", default="pending")
    s.add_argument("--priority", type=int)
    s.add_argument("--notes")
    s.add_argument("--workdir")
    s.add_argument("--harness", help="coding agent for this task's worker "
                                     "(claude|opencode|codex|...); default claude")
    s.add_argument("--model", help="model override for the harness (e.g. opus, "
                                   "zai/glm-4.7); default = the harness's default")
    s.add_argument("--runhost", help="named machine (sources.json runhosts key) the "
                                     "worker runs on; default = this machine")
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
    s.add_argument("--harness")
    s.add_argument("--model")
    s.add_argument("--runhost")
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
    s.add_argument("--set-status",
                   help="also set the task's status (e.g. blocked/awaiting-review/"
                        "done) in the same transaction; requires --task")
    s.set_defaults(func=cmd_log_event)

    s = sub.add_parser("set-session")
    s.add_argument("--task", required=True)
    s.add_argument("--session-id", required=True)
    s.add_argument("--workdir")
    s.add_argument("--harness", help="what ran it (defaults to the task's harness)")
    s.add_argument("--model", help="what it was dispatched with (defaults to the task's)")
    s.set_defaults(func=cmd_set_session)

    s = sub.add_parser("sessions",
                       help="every session that has ever run this task (append-only)")
    s.add_argument("--task", required=True)
    s.set_defaults(func=cmd_sessions)

    s = sub.add_parser(
        "backfill-sessions",
        help="reconstruct task_sessions from the append-only `dispatched` event log "
             "(for tasks dispatched before task_sessions existed, or whose session_id "
             "was destroyed by the old clear-session sweep)")
    s.set_defaults(func=cmd_backfill_sessions)

    s = sub.add_parser(
        "clear-session",
        help="DESTRUCTIVE: NULL a task's session_id. Only for an id recorded by "
             "MISTAKE — never to mean 'the worker stopped' (that breaks resume + cost)")
    s.add_argument("--task", required=True)
    s.set_defaults(func=cmd_clear_session)

    s = sub.add_parser(
        "reroute",
        help="clear a task's harness/model so the next spawn forces a fresh pwc route "
             "(for re-scoped tasks whose routing is stale)")
    s.add_argument("--task", required=True)
    s.add_argument("--reason", help="why the routing is being cleared")
    s.set_defaults(func=cmd_reroute)

    s = sub.add_parser(
        "archive",
        help="remove a task from the board WITHOUT marking it done (preserves status)")
    s.add_argument("--task", required=True)
    s.add_argument("--reason", help="why it's leaving the board (required unless --unarchive)")
    s.add_argument("--unarchive", action="store_true",
                   help="put an archived task back on the board")
    s.set_defaults(func=cmd_archive)

    s = sub.add_parser("promote")
    s.add_argument("--task", required=True, help="current id or alias")
    s.add_argument("--new-id", required=True, help="new canonical id, e.g. a Jira key")
    s.set_defaults(func=cmd_promote)

    s = sub.add_parser("merge", help="absorb one task into another (they're one piece of work)")
    s.add_argument("--from", dest="from", required=True,
                   help="task absorbed and deleted; its id becomes an alias of --into")
    s.add_argument("--into", required=True, help="surviving task; inherits refs + history")
    s.set_defaults(func=cmd_merge)

    s = sub.add_parser("export", help="dump the workspace's tasks/refs/events/aliases "
                                      "as JSON (for hub migration or backup)")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("import", help="load an export dump into an EMPTY workspace "
                                      "(JSON on stdin via --json -)")
    s.add_argument("--json", metavar="-", required=True)
    s.set_defaults(func=cmd_import)

    return p


def _run_one(cmd: str, args, workspace) -> None:
    """Run one op against exactly one workspace (local sqlite or hub)."""
    args.workspace = str(workspace) if workspace else args.workspace
    store = store_config(args.workspace)
    if store.get("store") == "hub":
        if cmd == "init":
            emit({"store": "hub", "url": store["url"],
                  "workspace": store["workspace"],
                  "note": "hub-backed workspace — no local task database to init"})
            return
        import hub_client
        hub_client.run(cmd, args, store)
        return
    args.func(args)


# Ops whose --task names a task that does NOT exist yet, so it cannot be used to
# infer a workspace: they CREATE. The caller must say where it lands.
_CREATE_OPS = frozenset({"add-task", "init", "import"})


def _multi_workspace(args, roots) -> bool:
    """Handle an op invoked from a PARENT of several workspaces. True if handled.

    Reads fan out across every workspace and merge, each row tagged with the
    workspace it came from — that's the combined board, the whole point of standing
    in ~/work. Writes do NOT fan out: a write belongs to exactly one board, so we
    resolve it to one workspace or refuse (see resolve_task_workspace).
    """
    cmd = args.cmd

    # Reads that TARGET one task (detail, find-session) are lookups, not sweeps:
    # fanning out returns one real hit plus a pile of misses. Resolve, then read
    # from the single workspace that actually holds it.
    if cmd in READ_OPS and getattr(args, "task", None):
        _run_one(cmd, args, resolve_task_workspace(args.task, roots))
        return True

    if cmd in READ_OPS:
        merged = []
        for root in roots:
            name = workspace_name(root)
            out = _capture(cmd, args, root)
            if out is None:
                continue
            if isinstance(out, list):
                for row in out:
                    if isinstance(row, dict):
                        row["workspace"] = name
                    merged.append(row)
            elif isinstance(out, dict):
                out["workspace"] = name
                merged.append(out)
        emit(merged)
        return True

    # A write that CREATES has nothing to infer from — its --task is a brand-new id
    # that by definition exists nowhere yet. Ask.
    if cmd in _CREATE_OPS:
        multi_workspace_fail(roots, cmd)
        return True

    # Any other write names an EXISTING task, and that task's own workspace decides —
    # uniquely, or we refuse rather than mutate the wrong board.
    task = getattr(args, "task", None)
    if task:
        _run_one(cmd, args, resolve_task_workspace(task, roots))
        return True
    multi_workspace_fail(roots, cmd)
    return True


def _capture(cmd: str, args, root):
    """Run a read op in `root` and parse its JSON, or None if it failed."""
    import copy
    import io
    import json as _json
    import contextlib
    sub = copy.copy(args)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            _run_one(cmd, sub, root)
    except SystemExit:
        return None
    except Exception:  # noqa: BLE001 — one bad workspace must not kill the sweep
        return None
    text = buf.getvalue().strip()
    if not text:
        return None
    try:
        return _json.loads(text)
    except ValueError:
        return None


def main(argv=None):
    args = build_parser().parse_args(argv)

    # Standing in a PARENT of several workspaces (e.g. ~/work, holding smarta/ and
    # side-projects/)? Discovery walking UP finds nothing there, which used to be a
    # dead end. It's actually the coordination vantage point — so fan reads out and
    # pin writes down.
    if not args.workspace:
        roots = workspaces_below()
        if roots:
            _multi_workspace(args, roots)
            return

    # Hub-backed workspace? Route the operation over HTTPS instead of local
    # sqlite — the response is byte-compatible, so nothing downstream can tell.
    # `init` stays local-ish: on a hub workspace there is no local db to create.
    store = store_config(args.workspace)
    if store.get("store") == "hub":
        if args.cmd == "init":
            emit({"store": "hub", "url": store["url"],
                  "workspace": store["workspace"],
                  "note": "hub-backed workspace — no local task database to init"})
            return
        import hub_client
        hub_client.run(args.cmd, args, store)
        return
    try:
        args.func(args)
    except FileNotFoundError as e:
        fail(str(e))
    except Exception as e:  # noqa: BLE001 — surface any error as a clean diagnostic
        fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
