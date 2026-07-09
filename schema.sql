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
  session_id    TEXT,                          -- pre-allocated worker session uuid (NULL if none;
                                               -- claude-harness only — other harnesses can't
                                               -- pre-allocate, so their tasks keep this NULL)
  harness       TEXT,                          -- which coding agent runs this task's worker
                                               -- (claude|opencode|codex|...); NULL = claude.
                                               -- Set at queue time by the routing policy
                                               -- (sources.json "routing"), user-overridable.
  model         TEXT,                          -- model override for the harness (e.g. "opus",
                                               -- "zai/glm-4.7"); NULL = the harness's default
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
