---
name: pwc-show-work
description: The PWC coordinator's all-tasks briefing. Reads the durable task database and presents a prioritized view of all in-flight work. Run it anytime — morning to orient, midday to check in, close to wrap up. This is the first thing to run after starting (or restarting) the coordinator.
---

# /pwc-show-work

The single interactive surface of the PWC coordinator. It reads the durable
SQLite task database and renders a prioritized view of all your tasks in flight, so the
user never has to hold the state of their work in their head. It produces the same
useful briefing whether the coordinator booted ten minutes ago or just now — all
state lives in the task database, not in this conversation.

This skill is built in layers, all present below: render, worker-status sweep,
staleness sweep, and a recap + archive. It reports purely from the local task
database — it does **not** re-scan external sources. Reading Jira/GitHub/Slack to
discover or re-check work is `/pwc-find-work`'s job alone; keeping that in one place
means `show-work` stays fast and side-effect-free, and there's exactly one path that
touches the outside world. `show-work` reports on work you're *already tracking*;
bringing in *new* work (and the source-reading that sets priorities and links
threads) is `/pwc-find-work`.

## Configuration

- **Scripts directory** (the shared PWC mechanism):
  `~/work/pwc/scripts`
  Referred to below as `$SCRIPTS`. All task database access goes through these scripts —
  never read or write the database directly.
- **Workspace**: the directory the coordinator is running in. The task database lives at
  `<workspace>/.pwc/taskdb.db` and the scripts discover it automatically from the
  current working directory, so no `--workspace` flag is normally needed.

## Tools

- `python3 $SCRIPTS/taskdb.py summary` — the always-loaded index: one status line
  per active task, archived tasks excluded. Returns a JSON array.
- `python3 $SCRIPTS/taskdb.py detail --task <id>` — full per-task detail: the
  structured fields, its references, and its event timeline. Use only when
  drilling into a specific task, not for the overview.
- `python3 $SCRIPTS/worker_status.py --json -` — reads a JSON list of
  `{task, session_id}` on stdin, returns each with `alive: true|false`. Tests
  whether each worker session is actually still running.
- `python3 $SCRIPTS/taskdb.py set-status-gone --task <id>` — mark a task whose
  worker has vanished as `gone` (needs triage), logging a `gone` event.
- `python3 $SCRIPTS/taskdb.py stale --threshold-days <N>` — active, **not parked**
  tasks untouched for longer than N days. The staleness-sweep candidates.
- `python3 $SCRIPTS/taskdb.py parked-aging --threshold-days <N>` — parked tasks
  aged beyond N days; the gentler "still waiting?" nudge.
- `python3 $SCRIPTS/taskdb.py events --since <ISO>` — events since a timestamp;
  the source for the recap.
- `python3 $SCRIPTS/taskdb.py log-event --kind recap --detail "..."`
  — record the recap.
- `python3 $SCRIPTS/taskdb.py archive --task <id>` — archive a done task so it
  leaves the active summary.

`show-work` uses no external-source tools — it reads only the local task database.

## Steps

1. **Load all tasks.** Run `python3 $SCRIPTS/taskdb.py summary`. If it errors
   with "no task database" the workspace isn't initialized — tell the user to run the PWC
   install/init for this workspace and stop.

2. **Worker-status sweep.** Collect every task in the summary that has a non-null
   `session_id` and a status that implies it should still be running (e.g.
   `active`, `blocked`, `awaiting-review` — not already `gone`, `done`, archived).
   Pass them as a JSON list of `{task, session_id}` to
   `python3 $SCRIPTS/worker_status.py --json -`. For each result with `alive: false`,
   run `taskdb.py set-status-gone --task <id>`.

   A dead session is **death, not outcome** — but it is *not* automatically `gone`.
   `set-status-gone` distinguishes two endings:
   - **Ended after reporting** — the worker logged a status change or a
     `/pwc-report-status` note after it was dispatched, then the session closed
     (the normal flow: do the work, report, close the tab). Here the task's status
     is *real and recent*, so the command **preserves it** and just detaches the
     finished `session_id`. Do not treat this as needing triage.
   - **Vanished** — the session died with nothing said since dispatch. Only this
     becomes `gone — needs triage` (resume, mark done, or drop); it may have left
     finished-but-unpushed work, so never infer done or failed.

   So you can run `set-status-gone` on every dead session uniformly — it won't
   clobber a worker's just-reported status. (Use `--force` only to deliberately mark
   a reported task `gone`.) If the sweep changed anything, re-run `summary`.

3. **Staleness sweep.** Run `taskdb.py stale --threshold-days 7` and
   `taskdb.py parked-aging --threshold-days 14`. Staleness is a **signal, not a
   verdict** — never auto-archive on age. Surface stale (non-parked) tasks and ask
   the user, per task, to keep or drop. Surface aged parked tasks as a softer
   nudge ("waiting N days — ping them?"). Take no action without the user's call.

4. **Recap + archive.** Summarize what changed since the last brief: read
   `events --since <last-brief-time>` (a task-DB-level `recap` event marks each
   brief, so "last brief" = the most recent `recap`). Write a concise one-line
   `log-event --kind recap` (task_id omitted = task-DB-level, not tied to one task)
   capturing the
   session's net activity. Then archive any task the user has confirmed done:
   `archive --task <id>` (drops it from the active summary).

5. **Render a prioritized briefing** from the summary JSON. Do not dump raw JSON —
   present a scannable view. Group and order so the user can triage at a glance:

   - Lead with anything needing attention: tasks whose `status` is `gone`
     (a worker vanished — needs triage) or `blocked`.
   - Then active work, ordered by `priority` (lower number = higher priority;
     `null` priority sorts last). Priority encodes "is someone waiting on me?" —
     `1` = blocks others, `2` = active, `3` = solo/research — so a `1` at the top is
     the most pressing. The `summary` call already returns rows in this order —
     preserve it.
   - Show parked tasks (`parked = 1`) in a separate, quieter group — they're
     waiting on something external and aren't asking for action right now. Note
     their `parked_reason`.

   For each task show: the internal id, type, title, status, and — if it has one —
   a hint that a worker session is attached (`session_id` is non-null). Keep each
   task to roughly one line; this is an index, not a report.

6. **Summarize the shape of your work** in a sentence or two: how many active,
   how many parked, anything flagged for attention (gone workers, stale tasks). This
   is the orientation the user is actually after.

7. **Offer next moves, don't take them.** End by pointing at what the user might do
   (e.g. "drill into a task, or run `/pwc-pick-work` for a suggestion"). Beyond the worker-status
   and staleness sweeps above (which only flag and, for dead workers, mark `gone`),
   do not dispatch work or otherwise mutate tasks unless the user asks.

## Notes

- **The task database is the source of truth, not this conversation.** If something here
  disagrees with what you remember from earlier in the session, the task database wins.
  Never invent tasks or statuses that aren't in the `summary` output.
- **Stay light.** `/pwc-show-work` should not pull full `detail` for every task — that
  defeats the two-tier design. Load `detail` only for a task the user is focusing on.
- **No external reads.** `show-work` never scans Jira/GitHub/Slack — that's
  `/pwc-find-work`'s sole job (it's also where task priorities get set and Slack
  threads get linked). If the briefing looks out of date with reality, the fix is to
  run `/pwc-find-work`, not to make `show-work` re-scan. This keeps one path to the
  outside world and `show-work` fast and side-effect-free.
- Archived (done-and-rolled-up) tasks are intentionally absent from `summary`. If
  the user asks about completed work, add `--include-archived`.
