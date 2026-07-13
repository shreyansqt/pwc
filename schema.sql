-- PWC ledger schema. One database per workspace, at <workspace>/.pwc/ledger.db.
-- All access goes through the coordinator via ledger.py; no hand-editing expected.
-- Idempotent: safe to apply repeatedly (CREATE ... IF NOT EXISTS).

PRAGMA journal_mode = WAL;        -- coordinator reads + worker appends concurrently
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,              -- meaningful id: a Jira key ("SMT-874") or
                                               -- <source>-<slug> ("slack-deploy-window"). Canonical;
                                               -- a task promoted to a Jira key keeps its old id in
                                               -- task_aliases. Resolve all lookups through aliases.
  type          TEXT NOT NULL,                 -- jira|pr-review|slack|email|doc|local|...
  title         TEXT NOT NULL,                 -- short description
  status        TEXT NOT NULL DEFAULT 'active',-- active|blocked|awaiting-review|done|gone|...
  priority      INTEGER,                       -- nullable; lower = higher priority
  notes         TEXT,                          -- freeform private notes (detail tier)
  parked        INTEGER NOT NULL DEFAULT 0,    -- 1 = explicitly parked; exempt from staleness sweep
  parked_reason TEXT,                          -- e.g. "awaiting review", "blocked on Priya"
  archived_at   TEXT,                          -- ISO8601 UTC when removed from the board WITHOUT
                                               -- completing it (not mine / dropped / someone else's
                                               -- work). NULL = on the board. Distinct from status='done'
                                               -- (finished): archiving hides a task while PRESERVING its
                                               -- real status, and records WHEN it left. Surfaced only
                                               -- via summary --archived.
  session_id    TEXT,                          -- the session to RESUME NEXT (the most recent one).
                                               -- A convenience pointer, NOT the provenance record —
                                               -- task_sessions holds every session that ever ran this
                                               -- task, append-only. Do NOT null this when a worker
                                               -- dies: a dead process is not a gone session (the
                                               -- transcript persists and stays resumable), and
                                               -- clearing it breaks both resume and cost measurement.
                                               -- Liveness is computed by pgrep, never stored here.
  harness       TEXT,                          -- which coding agent runs this task's worker
                                               -- (claude|opencode|codex|...); NULL = claude.
                                               -- Set at queue time by the routing policy
                                               -- (sources.json "routing"), user-overridable.
  model         TEXT,                          -- model override for the harness (e.g. "opus",
                                               -- "zai/glm-4.7"); NULL = the harness's default
  runhost       TEXT,                          -- named machine the worker runs on (key into
                                               -- sources.json "runhosts"); NULL = this machine.
                                               -- Remote workers run inside tmux over SSH, so they
                                               -- survive the laptop sleeping.
  workdir       TEXT,                          -- resolved cwd for dispatch/resume
  inline        INTEGER NOT NULL DEFAULT 0,    -- 1 = handled inline (informational)
  created_at    TEXT NOT NULL,                 -- ISO8601 UTC
  updated_at    TEXT NOT NULL,                 -- touched on any structured-field change; for a
                                               -- done task, doubles as its "done at" (board window)
  last_event_at TEXT                           -- cache of latest events.at for this task (staleness)
);
-- No archiving: the board (summary) shows all not-done tasks plus done tasks closed
-- within a recent window; older done tasks age off on their own. See cmd_summary.

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
-- idx_tasks_archived (on archived_at) is created in pwc_db._migrate(), AFTER the
-- column is ALTER-added — schema.sql runs before that ALTER on a pre-existing DB,
-- so creating it here would reference a not-yet-existing column.

-- Old ids a task has been known by. When a task gains a Jira key it is *promoted*:
-- its canonical tasks.id becomes the key, and its prior id is recorded here so old
-- references (events, the user's memory, a seeded worker) still resolve. Every
-- `--task <id>` lookup checks tasks.id first, then falls back to this table.
CREATE TABLE IF NOT EXISTS task_aliases (
  alias      TEXT PRIMARY KEY,                 -- a former id, e.g. "slack-deploy-window"
  task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,  -- current canonical id
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aliases_task ON task_aliases(task_id);

-- Typed, multi-valued reference set. Identity refs (for inbound matching) and
-- working-context refs (for dispatch). Normalized so the deferred matcher can
-- query "find the task whose identity ref = this Jira key".
CREATE TABLE IF NOT EXISTS task_refs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,                    -- identity | working
  ref_type   TEXT NOT NULL,                    -- jira_key|slack|pr|branch|workdir|pr_link|...
  value      TEXT NOT NULL,                    -- normalized raw id (e.g. "SMT-874", "owner/repo#45")
  label      TEXT,                             -- display-only human label / permalink
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_refs_identity ON task_refs(kind, ref_type, value);
CREATE INDEX IF NOT EXISTS idx_refs_task ON task_refs(task_id);

-- Append-only. The history (for /brief's recap) AND the per-task running
-- narrative (a task's timeline is its events). Never updated, never deleted.
CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,  -- NULL = ledger-level event (not tied to one task)
  at      TEXT NOT NULL,                       -- ISO8601 UTC
  source  TEXT NOT NULL,                       -- coordinator|worker|brief|system
  kind    TEXT NOT NULL,                       -- created|dispatched|status|blocked|
                                               -- awaiting-review|done|reconcile|new-task|
                                               -- stale-flag|recap|gone|note
  detail  TEXT                                 -- freeform message; the per-task narrative line
);

CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, at);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);

-- Every session that has EVER run this task. Append-only; nothing is ever deleted.
--
-- A task does not have *a* session — it has a HISTORY of them, and this is not an
-- edge case. You resume a task days later (same session, reopened), a worker crashes
-- and you retry (new session), or — now that routing exists — you re-run the same task
-- on a different model (new session, new harness). Real data from this workspace:
-- yotp-find-testers was dispatched 3x on ONE session (resume working correctly), while
-- pwc-route-tests ran on TWO different sessions in one day (the first opencode worker
-- was killed after a seed bug, the second did the work).
--
-- `tasks.session_id` is a single slot, so it silently OVERWRITES: pwc-route-tests' first
-- session — with real tokens spent — vanished from the task row entirely. And because
-- one slot cannot express "no live worker," /pwc-show-work's sweep used to NULL it when
-- a worker died (`clear-session`) — i.e. exactly when a task FINISHED — destroying the
-- only pointer to its transcript. That made finished tasks BOTH unresumable (start-work
-- would launch cold instead of `claude --resume <uuid>`) and unpriceable (`pwc cost`
-- finds the transcript by that id). This table is the fix: provenance lives here,
-- permanently, and tasks.session_id degrades to a convenience pointer.
--
-- Liveness is NEVER stored — `pwc worker-status` runs pgrep and computes it on demand.
-- A dead PROCESS is not a gone SESSION: the transcript persists on disk and stays
-- resumable indefinitely.
CREATE TABLE IF NOT EXISTS task_sessions (
  task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL,                    -- the HARNESS's session id (claude uuid,
                                               -- opencode ses_…, codex uuid) — the id in
                                               -- the worker process's argv, NOT an iTerm id
  harness    TEXT,                             -- which agent ran it (claude|opencode|codex)
  model      TEXT,                             -- the model it was dispatched with
  started_at TEXT NOT NULL,                    -- ISO8601 UTC of the dispatch
  PRIMARY KEY (task_id, session_id)            -- a resumed session is the SAME row
                                               -- (re-dispatch just updates started_at)
);

CREATE INDEX IF NOT EXISTS idx_task_sessions_task ON task_sessions(task_id, started_at);
CREATE INDEX IF NOT EXISTS idx_task_sessions_session ON task_sessions(session_id);

-- What a session actually CONSUMED. TOKENS, not dollars — deliberately.
--
-- Dollars are a derived view, not the measurement. Prices move constantly (the very
-- first `models fetch` produced 44 changes), so a stored dollar figure is welded to
-- whatever the table said the moment it was computed, and history stops being
-- reproducible: you could never re-ask "what would last month have cost if I'd
-- routed it to DeepSeek instead?" — the tokens would be gone. Store the tokens and
-- cost becomes a function you can re-run against ANY price set: today's table, last
-- month's, or a hypothetical one. That question is the entire point of the routing
-- engine (is the €180 plan still worth it?), so the data model has to be able to
-- answer it.
--
-- It also outlives the harnesses. Claude transcripts get pruned, opencode's sqlite
-- gets vacuumed, codex rollouts age out — persisting the counts here means the spend
-- record survives the storage it was read from.
--
-- One row per (task, session, model). Re-measured, not appended: `pwc cost --task X`
-- re-reads the session live and UPSERTs, because a worker session keeps spending
-- after its task is marked done (the follow-on skill tweaks, the docs pass), and
-- that spend is just as real. task_id is NULLABLE on purpose: the coordinator's own
-- session and inline tasks burn tokens with no task attached, and a spend report
-- that silently omitted them would understate the bill it exists to measure.
CREATE TABLE IF NOT EXISTS task_usage (
  task_id     TEXT REFERENCES tasks(id) ON DELETE CASCADE,  -- NULL = untracked session
                                                -- (coordinator / inline / ad-hoc)
  session_id  TEXT NOT NULL,                    -- the harness session these tokens came from
  harness     TEXT NOT NULL,                    -- claude|opencode|codex — which store was read
  model       TEXT,                             -- the model that ACTUALLY ran (ground truth from
                                                -- the transcript), which may differ from the model
                                                -- the router picked — that gap is worth seeing
  tokens_in     INTEGER NOT NULL DEFAULT 0,     -- uncached input
  tokens_out    INTEGER NOT NULL DEFAULT 0,     -- output (incl. reasoning tokens)
  cache_read    INTEGER NOT NULL DEFAULT 0,     -- DOMINATES agentic sessions — a live PWC worker
                                                -- logged 32M of these against 30k input, so any
                                                -- cost model ignoring them is wrong ~250x
  cache_write   INTEGER NOT NULL DEFAULT 0,
  measured_at TEXT NOT NULL,                    -- ISO8601 UTC of the last re-read
  PRIMARY KEY (session_id, model)               -- one row per model used within a session
                                                -- (a session can switch models mid-flight)
);

CREATE INDEX IF NOT EXISTS idx_usage_task ON task_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_usage_measured ON task_usage(measured_at);
