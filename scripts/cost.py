#!/usr/bin/env python3
"""`pwc cost` — what a task ACTUALLY consumed, measured from the harness's own store.

Measured, never estimated. An estimate is arithmetic done before the work happens
(a rack rate times a token count you don't have yet); the actual number is sitting
right there in the harness's session store afterward. Since the whole point of the
routing engine is to answer "is the €180 plan still worth it?", that question
deserves real numbers.

TOKENS ARE STORED; DOLLARS ARE DERIVED. Every read writes the four token counts into
`task_usage` and prices them only on the way out. Prices move (the first `models
fetch` alone produced 44 changes), so a stored dollar figure would freeze history
against a stale table and make "what would this have cost on DeepSeek?" unanswerable.
Tokens keep that question open forever. See schema.sql's task_usage comment.

Re-read live, never snapshotted. A worker session KEEPS SPENDING after its task is
marked done — the follow-up skill tweak, the docs pass, the "one more thing." So
cost is not an event captured at close; it's a property of the session, recomputed
whenever you ask. `--task X` re-reads and UPSERTs, so the number always reflects
everything that session has done to date.

Where the numbers come from (each harness stores usage its own way — verified live
2026-07-13, do not assume):
  claude   — ~/.claude/projects/<cwd-slug>/<uuid>.jsonl; each turn's message.usage
             carries input/output/cache_read/cache_creation. SUMMED across turns.
  codex    — ~/.codex/sessions/YYYY/MM/DD/rollout-*-<uuid>.jsonl; `token_count`
             event_msgs carry a RUNNING TOTAL (total_token_usage), so take the LAST
             one — summing them would multiply-count the whole session. Its
             `cached_input_tokens` is a SUBSET of `input_tokens`, so uncached input
             is the difference (double-billing otherwise).
  opencode — ~/.local/share/opencode/opencode.db, `session` table: token columns
             AND a `cost` it already computed. We still store tokens (so the row is
             re-pricable like every other) but report opencode's own cost as
             `harness_reported_cost` — for the metered OpenRouter models that IS the
             invoice, and it's more authoritative than our arithmetic.

A caveat this prints rather than hides: for claude/codex you are on a SUBSCRIPTION,
so their dollar figure is fair-value-at-rack-rate — what these tokens would cost on
the open market — NOT money that left your account. That is exactly the number that
tells you whether a cheaper plan would cover your usage. For opencode/OpenRouter it
is real money.

Usage:
  cost.py --task <id>              # re-read that task's session(s), upsert, price
  cost.py --report [--since 30d]   # roll up all recorded usage, per harness/model
  cost.py --sweep                  # re-read every known session, then report
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import models as models_mod
from _common import (days_ago_iso, emit, fail, find_workspace_root, now_iso,
                     store_config)

_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
_OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

_ZERO = {"tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0}

_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_usage (
  task_id     TEXT,
  session_id  TEXT NOT NULL,
  harness     TEXT NOT NULL,
  model       TEXT,
  tokens_in   INTEGER NOT NULL DEFAULT 0,
  tokens_out  INTEGER NOT NULL DEFAULT 0,
  cache_read  INTEGER NOT NULL DEFAULT 0,
  cache_write INTEGER NOT NULL DEFAULT 0,
  measured_at TEXT NOT NULL,
  PRIMARY KEY (session_id, model)
);
CREATE INDEX IF NOT EXISTS idx_usage_task ON task_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_usage_measured ON task_usage(measured_at);
"""


def usage_db(workspace=None) -> sqlite3.Connection:
    """Usage lives in its own LOCAL sqlite at <workspace>/.pwc/usage.db — even when
    the task database itself is hub-backed.

    Why a sidecar rather than a table in the task store: usage is *derived
    measurement*, re-readable at any time from the harness transcripts on THIS
    machine. It is inherently machine-local (the transcripts are), so pushing it to a
    shared hub would mean syncing numbers that only one machine can produce or
    verify. Keeping it local also means `pwc cost` works identically on a local and a
    hub workspace, instead of silently failing on hub ones — which is exactly what
    the first end-to-end run did before this existed.
    """
    root = Path(workspace).resolve() if workspace else find_workspace_root()
    path = root / ".pwc" / "usage.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(_USAGE_SCHEMA)
    return conn


def _task_row(task_id: str, workspace=None) -> dict | None:
    """Fetch a task through the SAME path everything else uses (`pwc detail`), so
    this works on local AND hub-backed workspaces rather than reaching past the
    store abstraction into a local sqlite file that may not exist."""
    cmd = ["pwc"]
    if workspace:
        cmd += ["--workspace", workspace]
    cmd += ["detail", "--task", task_id]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        fail(f"could not read task {task_id!r}: {e}")
    if out.returncode != 0:
        fail((out.stderr or out.stdout).strip() or f"no task {task_id!r}")
    try:
        return json.loads(out.stdout)["task"]
    except (ValueError, KeyError):
        fail(f"unexpected `pwc detail` output for {task_id!r}")


def _all_sessions(workspace=None) -> list[dict]:
    """Every task carrying a session id — via `pwc summary --all` (store-agnostic)."""
    cmd = ["pwc"]
    if workspace:
        cmd += ["--workspace", workspace]
    cmd += ["summary", "--all"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        fail(f"could not list tasks: {e}")
    if out.returncode != 0:
        fail((out.stderr or out.stdout).strip())
    try:
        rows = json.loads(out.stdout)
    except ValueError:
        fail("unexpected `pwc summary` output")
    return [r for r in rows if r.get("session_id")]


# ── readers: one per harness, each returning {model: {four token counts}} ──────
def read_claude(session_id: str) -> dict[str, dict]:
    """Sum per-turn usage out of the session transcript, split by the model that ran.

    The transcript path depends on the worker's cwd, which we don't know here — so
    glob every project dir for this session id. Cheap (one stat per project) and it
    means a task whose workdir moved still resolves.
    """
    matches = list(_CLAUDE_PROJECTS.glob(f"*/{session_id}.jsonl"))
    if not matches:
        return {}
    by_model: dict[str, dict] = {}
    for path in matches:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            if not usage:
                continue
            model = msg.get("model") or "unknown"
            # Claude Code stamps non-model turns (tool plumbing, interrupts) with
            # `<synthetic>` and zero usage. Recording those would litter task_usage
            # with unpriceable zero rows and make the report claim it couldn't price
            # a model that never actually ran.
            if model == "<synthetic>":
                continue
            acc = by_model.setdefault(model, dict(_ZERO))
            acc["tokens_in"] += usage.get("input_tokens") or 0
            acc["tokens_out"] += usage.get("output_tokens") or 0
            acc["cache_read"] += usage.get("cache_read_input_tokens") or 0
            acc["cache_write"] += usage.get("cache_creation_input_tokens") or 0
    return by_model


def _find_model(obj):
    """Codex records its model name NESTED at no fixed path (it turns up under
    `collaboration_mode.settings.model`, among others) and NOT in session_meta — so a
    fixed lookup returns None, which priced whole codex sessions at $0.00 against 59M
    real tokens. Search for it instead of guessing its location."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "model" and isinstance(val, str):
                return val
            found = _find_model(val)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_model(item)
            if found:
                return found
    return None


def read_codex(session_id: str) -> dict[str, dict]:
    """Take the LAST token_count event — codex reports a running cumulative total,
    so summing them would count the whole session once per turn."""
    matches = glob.glob(str(_CODEX_SESSIONS / f"*/*/*/rollout-*-{session_id}.jsonl"))
    if not matches:
        return {}
    latest = None
    model = None
    for path in matches:
        try:
            lines = Path(path).read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            payload = rec.get("payload") or {}
            if model is None:
                model = _find_model(payload)
            if payload.get("type") == "token_count":
                total = (payload.get("info") or {}).get("total_token_usage")
                if total:
                    latest = total
    model = model or "unknown"
    if not latest:
        return {}
    cached = latest.get("cached_input_tokens") or 0
    # codex's input_tokens INCLUDES the cached ones; subtract so they aren't billed
    # at the (much higher) uncached input rate.
    uncached = max((latest.get("input_tokens") or 0) - cached, 0)
    return {model: {
        "tokens_in": uncached,
        "tokens_out": (latest.get("output_tokens") or 0)
                      + (latest.get("reasoning_output_tokens") or 0),
        "cache_read": cached,
        "cache_write": 0,  # codex doesn't report cache writes separately
    }}


def read_opencode(session_id: str) -> tuple[dict[str, dict], float | None]:
    """opencode already computed the cost — return its tokens AND its own figure."""
    if not _OPENCODE_DB.exists():
        return {}, None
    try:
        conn = sqlite3.connect(f"file:{_OPENCODE_DB}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT model, cost, tokens_input, tokens_output, tokens_reasoning, "
            "       tokens_cache_read, tokens_cache_write "
            "FROM session WHERE id = ?", (session_id,)).fetchone()
        conn.close()
    except sqlite3.Error:
        return {}, None
    # opencode stores `model` as a JSON OBJECT, not a string:
    #   {"id": "deepseek/deepseek-v4-pro", "providerID": "openrouter", ...}
    # Reading it raw put a JSON blob in the model column and made it unpriceable.
    model = row["model"] or ""
    if model.startswith("{"):
        try:
            model = json.loads(model).get("id") or "unknown"
        except ValueError:
            model = "unknown"
    return {model or "unknown": {
        "tokens_in": row["tokens_input"] or 0,
        "tokens_out": (row["tokens_output"] or 0) + (row["tokens_reasoning"] or 0),
        "cache_read": row["tokens_cache_read"] or 0,
        "cache_write": row["tokens_cache_write"] or 0,
    }}, row["cost"]


def read_session(harness: str, session_id: str) -> tuple[dict[str, dict], float | None]:
    if harness == "claude":
        return read_claude(session_id), None
    if harness == "codex":
        return read_codex(session_id), None
    if harness == "opencode":
        return read_opencode(session_id)
    return {}, None


# ── pricing: tokens -> dollars, against the CURRENT table ─────────────────────
def _price_row(usage: dict, model_row: dict | None) -> float | None:
    """Price four token counts against a table row. None if we can't price it —
    a null cost is honest; a fabricated one silently corrupts the whole report."""
    if not model_row:
        return None
    price = models_mod.price_of(model_row)
    fallback = price["cost_in"]
    per = {
        "tokens_in": price["cost_in"],
        "tokens_out": price["cost_out"],
        "cache_read": price["cache_read"] or fallback,
        "cache_write": price["cache_write"] or fallback,
    }
    return round(sum(usage.get(k, 0) * per[k] for k in per) / 1_000_000, 6)


def _match_model(actual: str, table_models: list[dict]) -> dict | None:
    """Map the model string a HARNESS reports to a table row.

    The harness reports its own name for the model it ran (claude: 'claude-opus-4-8';
    opencode: 'deepseek/deepseek-v4-pro'), which matches neither our dispatch id nor
    always our catalog id — so try several joins before giving up rather than
    silently pricing at zero.
    """
    if not actual:
        return None
    norm = actual.lower()
    for row in table_models:
        cands = [row.get("catalog_id", ""), row.get("model", ""), row.get("key", "")]
        for c in cands:
            if c and (c.lower() == norm or norm.endswith(c.lower().split("/")[-1])):
                return row
    # last resort: the catalog id's tail (opus-4.8 ~ claude-opus-4-8)
    for row in table_models:
        tail = (row.get("catalog_id") or "").split("/")[-1].replace(".", "-").lower()
        if tail and tail in norm.replace(".", "-"):
            return row
    return None


# ── writes: measure a session and upsert its tokens ───────────────────────────
def measure(conn, *, task_id: str | None, session_id: str, harness: str) -> list[dict]:
    """Re-read the session's store and UPSERT one row per model it used."""
    by_model, reported = read_session(harness, session_id)
    written = []
    ts = now_iso()
    for model, usage in by_model.items():
        conn.execute(
            "INSERT INTO task_usage (task_id, session_id, harness, model, tokens_in, "
            "  tokens_out, cache_read, cache_write, measured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(session_id, model) DO UPDATE SET "
            "  task_id=excluded.task_id, harness=excluded.harness, "
            "  tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out, "
            "  cache_read=excluded.cache_read, cache_write=excluded.cache_write, "
            "  measured_at=excluded.measured_at",
            (task_id, session_id, harness, model, usage["tokens_in"],
             usage["tokens_out"], usage["cache_read"], usage["cache_write"], ts),
        )
        written.append({"model": model, **usage})
    return written, reported


def _task_sessions(task_id: str, workspace=None) -> list[dict]:
    """EVERY session that ever ran this task (via `pwc sessions`), not just the
    latest. A task is routinely run by more than one session — resumed days later,
    retried after a crash, re-run on a different model — and `tasks.session_id` holds
    only the most recent, so pricing off that alone silently under-reports every task
    that was ever retried."""
    cmd = ["pwc"]
    if workspace:
        cmd += ["--workspace", workspace]
    cmd += ["sessions", "--task", task_id]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout) if out.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def cmd_task(args):
    row = _task_row(args.task, args.workspace)
    conn = usage_db(args.workspace)

    # Price EVERY session this task ever ran, not just the current one. Fall back to
    # tasks.session_id for a task dispatched before task_sessions existed and not yet
    # backfilled (`pwc backfill-sessions`).
    sessions = _task_sessions(row["id"], args.workspace)
    if not sessions and row["session_id"]:
        sessions = [{"session_id": row["session_id"], "harness": row["harness"],
                     "model": row["model"]}]
    if not sessions:
        emit({"task": args.task, "cost_usd": None, "tokens": None,
              "why": "no session recorded — an inline task, or one never dispatched, "
                     "has no transcript to measure"})
        return

    written, reported = [], None
    with conn:
        for sess in sessions:
            harness = sess.get("harness") or row["harness"] or "claude"
            got, rep = measure(conn, task_id=row["id"],
                               session_id=sess["session_id"], harness=harness)
            written.extend(got)
            if rep is not None:
                reported = (reported or 0.0) + rep
    harness = row["harness"] or "claude"
    if not written:
        emit({"task": args.task, "cost_usd": None, "tokens": None,
              "sessions": [s["session_id"] for s in sessions],
              "why": f"no usage found in the harness store for "
                     f"{len(sessions)} session(s) (transcript pruned, or never ran)"})
        return
    table_models = models_mod.table()["models"]
    total = 0.0
    unpriced = []
    lines = []
    for entry in written:
        model_row = _match_model(entry["model"], table_models)
        usd = _price_row(entry, model_row)
        spent = any(entry.get(k) for k in _ZERO)
        if usd is None:
            # A ZERO-TOKEN session is not an unpriceable one — it just never ran (a
            # worker killed before it did anything, e.g. the seed never landed). It
            # contributes nothing, so it must not poison the task's total; only a
            # session that actually SPENT tokens we can't price is a real gap.
            if spent:
                unpriced.append(entry["model"])
            else:
                usd = 0.0
        else:
            total += usd
        lines.append({**entry, "cost_usd": usd,
                      "priced_as": model_row["key"] if model_row else None,
                      "ran": spent})
    out = {
        "task": args.task, "harness": harness,
        "session_id": row["session_id"],          # the one to resume next
        "sessions": [s["session_id"] for s in sessions],  # every one that ever ran it
        "session_count": len(sessions),
        "cost_usd": round(total, 4) if not unpriced else None,
        "by_model": lines,
        "note": _subscription_note(harness),
    }
    if reported is not None:
        out["harness_reported_cost_usd"] = round(reported, 4)
    if unpriced:
        out["unpriced_models"] = unpriced
        out["why"] = ("some models could not be priced against the table — add them "
                      "with `pwc models` rather than reporting a wrong total")
    emit(out)


def _subscription_note(harness: str) -> str:
    if harness in ("claude", "codex"):
        return ("fair-value at rack rate — these tokens ran on a SUBSCRIPTION, so "
                "this is what they would have cost on the open market, not money "
                "billed to you. That is the figure that tells you whether a cheaper "
                "plan would cover your usage.")
    return "real metered spend (OpenRouter) — this is money actually billed."


def cmd_report(args):
    """Roll up recorded usage, priced against the table AS IT IS TODAY."""
    conn = usage_db(args.workspace)
    params = []
    where = ""
    if args.since:
        where = "WHERE measured_at >= ?"
        params.append(days_ago_iso(_parse_since(args.since)))
    rows = conn.execute(
        f"SELECT * FROM task_usage {where} ORDER BY harness, model", params).fetchall()
    table_models = models_mod.table()["models"]

    by_harness: dict[str, dict] = {}
    unpriced = set()
    for r in rows:
        usage = {k: r[k] for k in _ZERO}
        model_row = _match_model(r["model"], table_models)
        usd = _price_row(usage, model_row)
        if usd is None:
            unpriced.add(r["model"])
            usd = 0.0
        h = by_harness.setdefault(r["harness"], {
            "harness": r["harness"], "cost_usd": 0.0, "sessions": 0,
            "tracked_tasks": 0, "untracked_sessions": 0, **dict(_ZERO)})
        h["cost_usd"] += usd
        h["sessions"] += 1
        if r["task_id"]:
            h["tracked_tasks"] += 1
        else:
            h["untracked_sessions"] += 1
        for k in _ZERO:
            h[k] += usage[k]

    for h in by_harness.values():
        h["cost_usd"] = round(h["cost_usd"], 4)
        h["note"] = _subscription_note(h["harness"])

    emit({
        "since": args.since,
        "total_cost_usd": round(sum(h["cost_usd"] for h in by_harness.values()), 4),
        "by_harness": sorted(by_harness.values(),
                             key=lambda h: -h["cost_usd"]),
        "unpriced_models": sorted(unpriced),
        "caveat": ("claude/codex figures are fair-value at rack rate (they ran on "
                   "subscriptions, so no money was billed); opencode/OpenRouter is "
                   "real metered spend. Compare the two to decide whether a plan "
                   "still earns its price."),
    })


def _parse_since(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("d"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        fail(f"--since must look like '30d' (got {s!r})")


def cmd_sweep(args):
    """Re-read every session the task DB knows about, then report.

    This is what keeps the numbers honest over time: a session that kept working
    after its task closed has spent more since it was last measured.
    """
    tasks = _all_sessions(args.workspace)
    conn = usage_db(args.workspace)
    measured = 0
    with conn:
        for t in tasks:
            written, _ = measure(conn, task_id=t["id"], session_id=t["session_id"],
                                 harness=t.get("harness") or "claude")
            if written:
                measured += 1
    emit({"sessions_measured": measured, "sessions_known": len(tasks),
          "note": "re-read live; run `pwc cost --report` for the rollup"})


_SESSION_RE = re.compile(r"\b(?:session\s+)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                         r"[0-9a-f]{4}-[0-9a-f]{12}|ses_[A-Za-z0-9]+)\b")


def discover_workspaces() -> list[Path]:
    """Every PWC workspace on this machine (any dir with a .pwc/).

    Transcripts are MACHINE-wide but a task board is per-workspace, so attribution
    that only consults the workspace you happen to be standing in will mis-file every
    session belonging to another one. (Measured: side-projects had 5 dispatches;
    smarta had 133 — reporting from side-projects alone made it look as though tasks
    simply weren't being dispatched, when in fact the busier board was never
    consulted.) So sweep them all.

    Kept shallow (~/work/* and ~/*) rather than a full-disk walk: PWC workspaces are
    top-level bodies of work by definition, and a deep scan of $HOME is slow and
    would wander into node_modules.
    """
    roots: list[Path] = []
    home = Path.home()
    seen = set()
    for parent in (home / "work", home):
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            marker = child / ".pwc"
            if marker.is_dir() and child not in seen:
                seen.add(child)
                roots.append(child)
    return roots


def _events_of(workspace) -> list[dict]:
    cmd = ["pwc", "--workspace", str(workspace), "events"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout) if out.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def session_owners(workspace=None, *, all_workspaces: bool = False) -> dict[str, str]:
    """{session_id: task_id} — mined from the EVENT LOG, not just the task rows.

    Two reasons the task rows alone are not enough, both measured:

    1. A task's `session_id` column is NOT durable: `clear-session` nulls it when
       /pwc-show-work sweeps a dead worker — precisely when the task FINISHES, so the
       link is erased exactly when you'd want to price the work. The append-only
       `dispatched` events survive that (side-projects: 4 links left on rows, 5
       recoverable from events).
    2. Transcripts are machine-wide; boards are per-workspace. Consulting only the
       current workspace mis-files every session that belongs to another one.
       `all_workspaces` sweeps every board on the machine.
    """
    owners: dict[str, str] = {}
    spaces = discover_workspaces() if all_workspaces else [workspace]
    for space in spaces:
        for e in _events_of(space) if space else []:
            if e.get("kind") != "dispatched" or not e.get("task_id"):
                continue
            m = _SESSION_RE.search(e.get("detail") or "")
            if m:
                owners[m.group(1)] = e["task_id"]
        # Live rows win — a running worker's current session is the freshest truth.
        for t in _all_sessions(space):
            owners[t["session_id"]] = t["id"]
    return owners


def cmd_backfill(args):
    """Ingest EVERY harness session on this machine, not just PWC-dispatched ones.

    `--sweep` only sees sessions the task DB knows about — which on a real machine is
    a tiny minority. The rest is the coordinator's own sessions, inline tasks, and
    every ad-hoc `claude`/`codex` run in a repo. Those burned real tokens, and a spend
    report that omits them understates the bill by an order of magnitude (measured
    here: the task DB knew 4 sessions; the disk held 104).

    So: walk the harness stores directly, attribute each session to a task where the
    task DB claims that session id, and record the rest with task_id NULL (the schema
    allows it precisely for this). Idempotent — re-running re-reads and upserts.
    """
    # Which sessions belong to a task? Mined from the append-only event log (the task
    # rows' session_id is erased by clear-session when a worker is swept), across
    # EVERY workspace on the machine (transcripts are machine-wide; boards are not).
    # Everything with no owner is untracked-but-real spend.
    owner = session_owners(args.workspace, all_workspaces=True)

    found = []
    # claude: one transcript per session, named <uuid>.jsonl
    for path in (_CLAUDE_PROJECTS).glob("*/*.jsonl"):
        found.append(("claude", path.stem))
    # codex: rollout-<ts>-<uuid>.jsonl
    for path in glob.glob(str(_CODEX_SESSIONS / "*/*/*/rollout-*.jsonl")):
        stem = Path(path).stem
        found.append(("codex", stem.split("-", 2)[-1][-36:]))
    # opencode: its own session table
    if _OPENCODE_DB.exists():
        try:
            oc = sqlite3.connect(f"file:{_OPENCODE_DB}?mode=ro", uri=True, timeout=5)
            for (sid,) in oc.execute("SELECT id FROM session"):
                found.append(("opencode", sid))
            oc.close()
        except sqlite3.Error:
            pass

    conn = usage_db(args.workspace)
    measured = attributed = 0
    empty = 0
    with conn:
        for harness, sid in found:
            written, _ = measure(conn, task_id=owner.get(sid), session_id=sid,
                                 harness=harness)
            if not written:
                empty += 1
                continue
            measured += 1
            if owner.get(sid):
                attributed += 1

    emit({
        "sessions_seen": len(found),
        "sessions_measured": measured,
        "attributed_to_tasks": attributed,
        "untracked": measured - attributed,
        "no_usage_found": empty,
        "note": ("untracked sessions (coordinator, inline, ad-hoc runs) are recorded "
                 "with task_id NULL — they are real spend and belong in the rollup. "
                 "Run `pwc cost --report` to see it."),
    })


def main(argv=None):
    p = argparse.ArgumentParser(prog="cost.py", description=__doc__)
    p.add_argument("--workspace", help="workspace root (default: discover from cwd)")
    p.add_argument("--task", help="measure this task's session(s) and price them")
    p.add_argument("--report", action="store_true", help="roll up recorded usage")
    p.add_argument("--sweep", action="store_true",
                   help="re-read every session the task DB knows about")
    p.add_argument("--backfill", action="store_true",
                   help="ingest EVERY harness session on this machine — including "
                        "coordinator/inline/ad-hoc ones with no task (the bulk of "
                        "real spend). Attributes to tasks via the dispatch event log.")
    p.add_argument("--since", help="report window, e.g. 30d")
    args = p.parse_args(argv)
    try:
        if args.backfill:
            cmd_backfill(args)
        elif args.sweep:
            cmd_sweep(args)
        elif args.report:
            cmd_report(args)
        elif args.task:
            cmd_task(args)
        else:
            fail("cost: pass --task <id>, --report, or --sweep")
    except FileNotFoundError as e:
        fail(str(e))
    except Exception as e:  # noqa: BLE001
        fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
