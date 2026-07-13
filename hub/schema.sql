-- PWC hub schema (D1). Mirrors the local schema.sql (one DB per workspace) as a
-- single shared D1 database serving many workspaces, distinguished by a
-- `workspace` column added to every table. See docs/hub-design.md, Decision 2.
--
-- D1 manages its own journal mode / durability — no PRAGMA journal_mode here.
-- `PRAGMA foreign_keys` is also omitted: D1 enforces FOREIGN KEY constraints by
-- default, and composite foreign keys (workspace, id) are supported, so child
-- tables declare a real composite FK back to tasks(workspace, id) rather than
-- enforcing the link in code.

CREATE TABLE IF NOT EXISTS tasks (
  workspace     TEXT NOT NULL,                -- logical workspace name (e.g. "smarta")
  id            TEXT NOT NULL,                -- meaningful id: a Jira key ("SMT-874") or
                                               -- <source>-<slug> ("slack-deploy-window"). Canonical
                                               -- within its workspace; a task promoted to a Jira key
                                               -- keeps its old id in task_aliases. Resolve all lookups
                                               -- through aliases.
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
  runhost       TEXT,                          -- named machine the worker runs on (key into
                                               -- sources.json "runhosts"); NULL = this machine.
                                               -- Remote workers run inside tmux over SSH, so they
                                               -- survive the laptop sleeping.
  workdir       TEXT,                          -- resolved cwd for dispatch/resume
  inline        INTEGER NOT NULL DEFAULT 0,    -- 1 = handled inline (informational)
  created_at    TEXT NOT NULL,                 -- ISO8601 UTC
  updated_at    TEXT NOT NULL,                 -- touched on any structured-field change; for a
                                               -- done task, doubles as its "done at" (board window)
  last_event_at TEXT,                          -- cache of latest events.at for this task (staleness)
  PRIMARY KEY (workspace, id)
);
-- No archiving: the board (summary) shows all not-done tasks plus done tasks closed
-- within a recent window; older done tasks age off on their own. See cmd_summary
-- (ported to the `summary` op in src/index.ts).

CREATE INDEX IF NOT EXISTS idx_tasks_ws_status ON tasks(workspace, status);
CREATE INDEX IF NOT EXISTS idx_tasks_ws_session ON tasks(workspace, session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_ws_archived ON tasks(workspace, archived_at);

-- Old ids a task has been known by. When a task gains a Jira key it is *promoted*:
-- its canonical tasks.id becomes the key, and its prior id is recorded here so old
-- references (events, the user's memory, a seeded worker) still resolve. Every
-- `task` lookup checks tasks.id first, then falls back to this table.
CREATE TABLE IF NOT EXISTS task_aliases (
  workspace  TEXT NOT NULL,
  alias      TEXT NOT NULL,                    -- a former id, e.g. "slack-deploy-window"
  task_id    TEXT NOT NULL,                     -- current canonical id (within this workspace)
  created_at TEXT NOT NULL,
  PRIMARY KEY (workspace, alias),
  FOREIGN KEY (workspace, task_id) REFERENCES tasks(workspace, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_aliases_ws_task ON task_aliases(workspace, task_id);

-- Typed, multi-valued reference set. Identity refs (for inbound matching) and
-- working-context refs (for dispatch). Normalized so the deferred matcher can
-- query "find the task whose identity ref = this Jira key".
CREATE TABLE IF NOT EXISTS task_refs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace  TEXT NOT NULL,
  task_id    TEXT NOT NULL,
  kind       TEXT NOT NULL,                    -- identity | working
  ref_type   TEXT NOT NULL,                    -- jira_key|slack|pr|branch|workdir|pr_link|...
  value      TEXT NOT NULL,                    -- normalized raw id (e.g. "SMT-874", "owner/repo#45")
  label      TEXT,                             -- display-only human label / permalink
  created_at TEXT NOT NULL,
  FOREIGN KEY (workspace, task_id) REFERENCES tasks(workspace, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_refs_ws_identity ON task_refs(workspace, kind, ref_type, value);
CREATE INDEX IF NOT EXISTS idx_refs_ws_task ON task_refs(workspace, task_id);

-- Append-only. The history (for /brief's recap) AND the per-task running
-- narrative (a task's timeline is its events). Never updated, never deleted.
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace TEXT NOT NULL,
  task_id   TEXT,                              -- NULL = workspace-level event (not tied to one task)
  at        TEXT NOT NULL,                     -- ISO8601 UTC
  source    TEXT NOT NULL,                     -- coordinator|worker|brief|system
  kind      TEXT NOT NULL,                     -- created|dispatched|status|blocked|
                                               -- awaiting-review|done|reconcile|new-task|
                                               -- stale-flag|recap|gone|note
  detail    TEXT,                              -- freeform message; the per-task narrative line
  FOREIGN KEY (workspace, task_id) REFERENCES tasks(workspace, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_ws_task ON events(workspace, task_id, at);
CREATE INDEX IF NOT EXISTS idx_events_ws_at ON events(workspace, at);

-- Every session that has EVER run a task. Append-only; nothing is ever deleted.
-- Mirrors the local schema.sql table of the same name — see there for the full why.
--
-- Short version: a task does not have *a* session, it has a HISTORY of them (resumed
-- days later, retried after a crash, re-run on a different model). `tasks.session_id`
-- is a single slot, so it silently overwrote — and because one slot cannot express
-- "no live worker", the old /pwc-show-work sweep NULLed it whenever a worker died,
-- i.e. exactly when a task FINISHED. That destroyed the only pointer to the session's
-- transcript, making finished tasks both unresumable (`claude --resume <uuid>`) and
-- unpriceable (`pwc cost` finds the transcript by that id). Provenance lives here now;
-- tasks.session_id is just "the one to resume next". Liveness is never stored — pgrep
-- computes it on demand.
CREATE TABLE IF NOT EXISTS task_sessions (
  workspace  TEXT NOT NULL,
  task_id    TEXT NOT NULL,
  session_id TEXT NOT NULL,                    -- the HARNESS's session id (claude uuid,
                                               -- opencode ses_…, codex uuid) — the id in
                                               -- the worker's argv, NOT an iTerm id
  harness    TEXT,                             -- claude|opencode|codex
  model      TEXT,                             -- what it was dispatched with
  started_at TEXT NOT NULL,                    -- ISO8601 UTC of the dispatch
  PRIMARY KEY (workspace, task_id, session_id),-- a RESUMED session is the SAME row
  FOREIGN KEY (workspace, task_id) REFERENCES tasks(workspace, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_sessions_ws_task
  ON task_sessions(workspace, task_id, started_at);
