---
name: pwc-show-task
description: Show one PWC task's full context — its fields, references (PRs, Slack threads, Jira keys), and event timeline. A worker uses it to (re)orient on its own task; the coordinator uses it to drill into any task. Pass a task id, or omit it in a worker to resolve the task from the running session.
---

# /pwc-show-task

Pull the full context of a single task from the task database — structured fields,
its refs, and its event history. Where `/pwc-show-work` is the all-tasks index,
`/pwc-show-task` is the one-task drill-down.

Two audiences, one skill:
- **A worker** runs it to (re)orient on *its own* task — after a context loss, or to
  check the latest refs/notes before acting. Unlike an opaque script line in the seed
  prose (which a fresh worker rightly won't run), this is a named, installed skill the
  worker can invoke deliberately — the trusted path back to its task.
- **The coordinator** runs it to drill into *any* task without leaving the briefing.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: the current directory; task database auto-discovered at
  `<workspace>/.pwc/taskdb.db`. (A worker runs inside a repo under the workspace, so
  discovery still resolves the same db; pass `--workspace` if it doesn't.)
- **Argument**: an optional task id (e.g. `SMT-921`). The `/pwc-start-work` seed states
  the worker's task id, so a worker usually has it. If omitted, the skill resolves the
  task from the running session (worker case).

## Tools

- `python3 $SCRIPTS/taskdb.py detail --task <id>` — the full per-task detail. The
  primary call. The JSON has exactly these top-level keys: `task` (the fields),
  `refs` (the references — note the key is `refs`, not `references`), `events` (the
  timeline), `aliases`. Read `refs` for PRs/Slack threads/Jira keys.
- `python3 $SCRIPTS/taskdb.py find-session --session-id <uuid>` — map a worker's
  `claude` session id back to its task (returns the summary row, or `null`). Used only
  when no task id was given.

## Steps

1. **Resolve the task id.**
   - **If an id was passed** (`/pwc-show-task SMT-921`), use it.
   - **Else, in a worker, infer the session id and look it up.** A worker is launched
     as `claude --session-id <uuid>`, so its own uuid is in its process args — find it
     with `pgrep -fl 'claude --session-id'` (or read it from the transcript path under
     `~/.claude/projects/`). Then `find-session --session-id <uuid>` returns the task
     row; take its `id`. If `find-session` returns `null`, no task carries this
     session — tell the user (the seed never recorded a session, or it was cleared)
     and stop.
   - **Else, in the coordinator with no id**, ask which task (or point at
     `/pwc-show-work`). Don't guess.

2. **Fetch the detail.** Run `taskdb.py detail --task <id>`. If it errors with
   `no task <id>`, surface that — the id is wrong (done tasks are never removed, so a
   valid id always resolves).

3. **Render it readably** — don't dump raw JSON. Present:
   - the task line: id, type, title, status, priority, parked + reason if parked;
   - the **refs** grouped by kind — identity refs (Jira keys, the canonical Slack
     thread) and working refs (PRs, related threads, epics), with their labels/links;
   - the **event timeline**, newest-relevant first, so the reader sees the narrative
     (what's been done, what it's blocked on, who's waiting).
   For a worker re-orienting, lead with what it most needs: the goal and the latest
   events. For the coordinator, a compact summary is fine.

   **Render times in the user's local timezone.** DB timestamps are UTC (trailing
   `Z`); convert them to the user's wall-clock TZ (e.g. Europe/Berlin → CEST/CET)
   before showing. The raw UTC string is misleading when used as-is in a briefing.

4. **Stop there.** This is read-only — it shows context, it does not act on the task
   or mutate anything. To record progress use `/pwc-report-status`; to start/resume
   work use `/pwc-start-work`.

## Notes

- **Read-only.** `/pwc-show-task` never writes — no status changes, no events. It's
  safe for a fresh worker to run (it does nothing but read its own task).
- **The task id is the durable handle.** The `/pwc-start-work` seed states it for
  exactly this reason; session-resolution is the fallback for when the id was lost.
- `detail --task <id>` shows any task by id regardless of how long ago it finished
  (there is no archiving; done tasks just age off the *board*, not the database).
