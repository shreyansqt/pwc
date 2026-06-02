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
staleness sweep, and a recap. It reports purely from the local task
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
  per task on the board. The board is every not-done task plus done tasks closed in
  the last ~2 days (a rolling "what just finished" timeline); older done tasks age
  off on their own. There is **no archiving**. `--all` shows every task ever;
  `--done-within-days N` adjusts the done window. Returns a JSON array.
- `python3 $SCRIPTS/taskdb.py detail --task <id>` — full per-task detail: the
  structured fields, its `refs`, and its event timeline (JSON keys: `task`, `refs`,
  `events`, `aliases` — the references key is `refs`, not `references`). Use only when
  drilling into a specific task, not for the overview.
- `python3 $SCRIPTS/sources.py mode` — the workspace's launch mode
  (`{"mode": "iterm2"|"desktop"}`, default `iterm2`). Read it before the
  worker-status sweep: the sweep is **iterm2-only** (see step 2).
- `python3 $SCRIPTS/worker_status.py --json -` — reads a JSON list of
  `{task, session_id}` on stdin, returns each with `alive: true|false`. Tests
  whether each worker session is actually still running. **iterm2 mode only** — a
  Desktop worker is not a local `claude` process, so `pgrep` can't see it.
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

`show-work` uses no external-source tools — it reads only the local task database.

## Steps

1. **Load all tasks.** Run `python3 $SCRIPTS/taskdb.py summary`. If it errors
   with "no task database" the workspace isn't initialized — tell the user to run the PWC
   install/init for this workspace and stop.

2. **Worker-status sweep (iterm2 mode only).** First read
   `python3 $SCRIPTS/sources.py mode`. **In `desktop` mode, skip this entire step** —
   a Desktop worker is a session the user opened, not a local `claude` process, so
   `pgrep` can't observe it and a "not alive" result would be meaningless (every such
   worker would be wrongly marked `gone`). In desktop mode, lean on reported status
   instead: a task's status is whatever the worker/user last recorded via
   `/pwc-report-status`, and if the user wants to know whether something is still in
   flight, **ask them** rather than inferring. Proceed to step 3.

   **In `iterm2` mode**, collect every task in the summary that has a non-null
   `session_id` and a status that implies it should still be running (e.g.
   `active`, `blocked`, `awaiting-review` — not already `gone` or `done`).
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

   **Verify before acting on a worker's reported outcome.** A worker's reported
   status (and the note that comes with it) is *secondhand* — it is what the worker
   believed when its session ended, and it can be stale, partial, or simply wrong.
   Preserving it in the DB is fine (above). But the moment you'd take an
   **irreversible or hard-to-undo action on the strength of that report** — closing a
   task as `done`, re-pointing it at a different ticket, merging it into another,
   dropping it — first **re-read the underlying source the worker cited** (the Slack
   thread, the PR, the Jira ticket) and confirm the outcome is what the worker claimed.
   Do not close or re-scope a task on the worker's note alone, and do not close on the
   *user's* recollection alone either (e.g. "Stella already replied / graded a
   ticket") — read the actual reply/ticket and reconcile. The user pointing you at it
   is the trigger to verify, not a substitute for verifying. This is the one place
   `show-work` is *allowed* to read an external source (normally find-work's job): a
   targeted re-read to confirm a specific outcome before a destructive write, not a
   re-scan for new work.

   **Update the task's context before the status change, not after.** When you do act,
   capture *what actually happened* first — `log-event --kind note` with the verified
   facts (what the source said, the decision, the new ticket/owner), and fix
   `title` / `workdir` / refs so they match reality — **then** change the status. A
   task closed (or re-pointed) with only a one-line "closed: done" note loses the
   context that the next person (or the future you) needs. The note that justifies the
   change must land with the change.

3. **Staleness sweep.** Run `taskdb.py stale --threshold-days 7` and
   `taskdb.py parked-aging --threshold-days 14`. Staleness is a **signal, not a
   verdict** — never auto-archive on age. Surface stale (non-parked) tasks and ask
   the user, per task, to keep or drop. Surface aged parked tasks as a softer
   nudge ("waiting N days — ping them?"). Take no action without the user's call.

4. **Recap.** Summarize what changed since the last brief: read
   `events --since <last-brief-time>` (a task-DB-level `recap` event marks each
   brief, so "last brief" = the most recent `recap`). Write a concise one-line
   `log-event --kind recap` (task_id omitted = task-DB-level, not tied to one task)
   capturing the session's net activity. There is **no archive step** — a done task
   stays on the board for ~2 days as a "just finished" line, then ages off the
   summary on its own. Nothing to file by hand.

5. **Render the briefing as ONE single table** from the summary JSON. Do not dump raw
   JSON, and do not split the board into per-section tables. Columns, in this exact
   order:

   | # | Status | Pri | ID | Desc |

   - **#** — running number, top to bottom across the whole table, so the user can act
     by number ("start 3").
   - **Status** — an **emoji + the status word**, which also carries the grouping (rows
     are sorted by status-band, so there's no separate Group column). Use exactly:
     - 🚨 `gone` — worker vanished, needs triage
     - 🟢 `active`
     - 🔵 `awaiting-review`
     - ⛔ `blocked`
     - 💤 `parked`
     - ✅ `done`

     Precedence when both apply: a `parked = 1` task renders as 💤 `parked` even if its
     underlying status is `blocked`/`active` (parked is the user-facing state).
   - **Pri** — the numeric priority (`1`/`2`/`3`, blank if null). Lower = higher;
     priority encodes "is someone waiting on me?" (`1` blocks others, `2` active,
     `3` solo/research).
   - **ID** — the task id (e.g. `SMT-944`, `slack-...`).
   - **Desc** — a **short description (≤ ~8 words) that identifies the task**, not a
     restatement of the id. Distil it from the title + latest event so the user can
     recognize the work at a glance (e.g. "mobile OCR fails since 20.05", "review BO
     auth PR #415"). **Every task gets one — never blank.** For a blocked/parked task,
     make the desc say what it's waiting on ("waiting on Maesn", "paused, resume
     later"). This column is the point of the briefing — it lets the user pick by
     recognition instead of decoding ids.

   **Sort the table by status-band in this order**, then by `Pri` (ascending, null
   last) within each band:
   1. 🚨 **gone** — needs triage (resume / mark done / drop). Note: `blocked` is NOT
      triage — it's a separate band below; don't lump blocked in with gone.
   2. 🟢 **active** / 🔵 **awaiting-review** — work in flight, ordered by priority.
   3. ⛔ **blocked** — waiting on a person/dependency/decision; Desc says on what.
   4. 💤 **parked** — waiting on something external / deliberately paused; Desc notes
      the `parked_reason`.
   5. ✅ **done** — recently-finished window; a recap of what closed (no action; don't
      call it "ready to archive" — there is no archiving).

   Keep each row to one line — this is an index, not a report.

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
  Even then, read it to *route and summarize* (status, refs, where it stands) — not to
  brief yourself on the underlying thread/PR/Jira content. That content is the worker's
  to re-derive (via `/pwc-show-task` and the live sources); the coordinator pulling it
  into its own context is exactly the pollution the two-tier design avoids.
- **No external reads.** `show-work` never scans Jira/GitHub/Slack — that's
  `/pwc-find-work`'s sole job (it's also where task priorities get set and Slack
  threads get linked). If the briefing looks out of date with reality, the fix is to
  run `/pwc-find-work`, not to make `show-work` re-scan. This keeps one path to the
  outside world and `show-work` fast and side-effect-free.
- **No archiving.** Done tasks stay on the board for ~2 days (a "just finished"
  timeline) then age off `summary` automatically — there's no archive step and
  nothing to file by hand. For older completed work, `summary --all` shows every
  task ever; `--done-within-days N` widens or narrows the window.
- **Render times in the user's local timezone.** All DB timestamps are stored in
  UTC (the trailing `Z` is literal — `taskdb.py` uses `now_iso()` from `_common.py`
  for everything). When showing a time in the briefing, **convert it to the user's
  local timezone** (e.g. Europe/Berlin → CEST in summer, CET in winter) and tag it
  if there's any room for ambiguity. Showing the raw UTC time and calling it
  "finished 08:57" when their wall clock said 10:57 is a real bug, not a cosmetic
  one — it makes the briefing actively wrong.
