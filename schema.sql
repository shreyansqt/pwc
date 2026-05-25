-- PWC ledger schema. One database per workspace, at <workspace>/.pwc/ledger.db.
-- All access goes through the coordinator via ledger.py; no hand-editing expected.
-- Idempotent: safe to apply repeatedly (CREATE ... IF NOT EXISTS).

PRAGMA journal_mode = WAL;        -- coordinator reads + worker appends concurrently
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,              -- coordinator-assigned stable id (e.g. "t_0007")
  type          TEXT NOT NULL,                 -- jira|pr-review|slack|email|doc|local|...
  title         TEXT NOT NULL,                 -- short description
  status        TEXT NOT NULL DEFAULT 'active',-- active|blocked|awaiting-review|done|gone|...
  priority      INTEGER,                       -- nullable; lower = higher priority
  notes         TEXT,                          -- freeform private notes (detail tier)
  parked        INTEGER NOT NULL DEFAULT 0,    -- 1 = explicitly parked; exempt from staleness sweep
  parked_reason TEXT,                          -- e.g. "awaiting review", "blocked on Priya"
  session_id    TEXT,                          -- pre-allocated worker session uuid (NULL if none)
  workdir       TEXT,                          -- resolved cwd for dispatch/resume
  inline        INTEGER NOT NULL DEFAULT 0,    -- 1 = handled inline (informational)
  created_at    TEXT NOT NULL,                 -- ISO8601 UTC
  updated_at    TEXT NOT NULL,                 -- touched on any structured-field change
  last_event_at TEXT,                          -- cache of latest events.at for this task (staleness)
  archived_at   TEXT                           -- non-NULL = archived; excluded from summary
);

CREATE INDEX IF NOT EXISTS idx_tasks_active ON tasks(archived_at);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);

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
                                               -- stale-flag|recap|archived|gone|note
  detail  TEXT                                 -- freeform message; the per-task narrative line
);

CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, at);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
