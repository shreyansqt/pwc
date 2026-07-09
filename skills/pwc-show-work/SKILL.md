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

- **CLI**: `pwc` — on PATH (installed by `install.sh` as `~/.local/bin/pwc`). All
  task database access goes through it — never read or write the database directly.
- **Workspace**: the directory the coordinator is running in. The task database lives at
  `<workspace>/.pwc/taskdb.db` and `pwc` discovers it automatically from the
  current working directory, so no `--workspace` flag is normally needed.

## Tools

- `pwc summary` — the always-loaded index: one status line
  per task on the board. The board is every not-done, **not-archived** task plus done
  tasks closed in the last ~2 days (a rolling "what just finished" timeline); older
  done tasks age off on their own. `--all` shows every non-archived task ever;
  `--archived` shows ONLY the archived (off-board, not-completed) set;
  `--done-within-days N` adjusts the done window. Returns a JSON array.
- `pwc detail --task <id>` — full per-task detail: the
  structured fields, its `refs`, and its event timeline (JSON keys: `task`, `refs`,
  `events`, `aliases` — the references key is `refs`, not `references`). Use only when
  drilling into a specific task, not for the overview.
- `pwc worker-status --json -` — reads a JSON list of
  `{task, session_id}` on stdin, returns each with `alive: true|false`. Tests
  whether each worker session is actually still running.
- `pwc clear-session --task <id>` — detach a finished/dead
  worker session from a task (the task's status is left as-is). Use this in the
  worker-status sweep when a session is `alive: false`. (The old `set-status-gone` is
  retired along with the `gone` status — a vanished worker just stays `in-progress`
  with its session detached.)
- `pwc stale --threshold-days <N>` — active, **not parked**
  tasks untouched for longer than N days. The staleness-sweep candidates.
- `pwc parked-aging --threshold-days <N>` — parked tasks
  aged beyond N days; the gentler "still waiting?" nudge.
- `pwc events --since <ISO>` — events since a timestamp;
  the source for the recap.
- `pwc log-event --kind recap --detail "..."`
  — record the recap.

`show-work` uses no external-source tools — it reads only the local task database.

## Steps

1. **Load all tasks.** Run `pwc summary`. If it errors
   with "no task database" the workspace isn't initialized — tell the user to run the PWC
   install/init for this workspace and stop.

2. **Worker-status sweep.** Collect every task in the summary that has a non-null
   `session_id` and is `in-progress` (the only status that implies a worker should be
   running — `pending`/`blocked`/`done` shouldn't have a live worker). Pass them as a
   JSON list of `{task, session_id}` to `pwc worker-status --json -`.

   A dead session is **death, not outcome.** For each `alive: false` result:
   - **Ended after reporting** — the worker logged a status change / note after
     dispatch, then closed (the normal flow: work, report, close the tab). The task's
     status is real and recent — leave it; just detach the finished `session_id`
     (`pwc clear-session --task <id>`).
   - **Vanished** — the session died with nothing said since dispatch. The task
     **stays `in-progress`** (it was mid-flight and is resumable) — do not invent a new
     status, do not infer done/failed (it may have unpushed work). Detach the session
     and note "worker died, resumable" so the user can resume or re-decide. Surface
     these in the briefing as the in-progress rows whose worker is no longer live.

   If the sweep changed anything, re-run `summary`. (There is no `gone` status anymore;
   a vanished worker is just an in-progress task with a dead session.)

   The sweep only covers **claude-harness workers** — they're the only ones with a
   pre-allocated `session_id` to test, so tasks on another harness (`harness` =
   opencode/codex/…) never enter it. For those, the task's status is whatever was
   last reported; if it matters whether one is still in flight, ask the user rather
   than inferring.

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

3. **Staleness sweep.** Run `pwc stale --threshold-days 7` and
   `pwc parked-aging --threshold-days 14`. Staleness is a **signal, not a
   verdict** — never auto-archive on age. Surface stale (non-parked) tasks and ask
   the user, per task, to keep or drop. Surface aged parked tasks as a softer
   nudge ("waiting N days — ping them?"). Take no action without the user's call.

4. **Recap.** Summarize what changed since the last brief: read
   `events --since <last-brief-time>` (a task-DB-level `recap` event marks each
   brief, so "last brief" = the most recent `recap`). Write a concise one-line
   `log-event --kind recap` (task_id omitted = task-DB-level, not tied to one task)
   capturing the session's net activity. A done task stays on the board for ~2 days as
   a "just finished" line, then ages off the summary on its own — nothing to file by
   hand. (Archiving is a separate, explicit act for *not-completed* work leaving the
   board — never an automatic step here; see the done-vs-archived note in step 5.)

5. **Render the briefing as a main table PLUS a small separate "Done" table.** Do not
   dump raw JSON.
   - The **main table** holds only the **active work**: `pending`, `in-progress`, and
     `blocked` rows. **`done` rows are NOT in the main table.**
   - Below it, render a **separate, smaller "✅ Recently finished" table** for the
     `done` tasks in the ~2-day window (columns `# | ID | Desc`). Keep it visually
     distinct and after the main board — it's a recap of what just closed, not a to-do.
     If there are no recent done tasks, omit the Done table entirely.

   Main table columns, in this exact order:

   | # | Status | Pri | ID | Dir | Desc |

   - **#** — running number, top to bottom across the whole table, so the user can act
     by number ("start 3").
   - **Status** — an **emoji + the status word**, which also carries the grouping (rows
     are sorted by status-band, so there's no separate Group column). There are exactly
     **four** statuses — use these and only these:
     - ⚪ `pending` — queued, ready to start, nobody on it yet
     - 🟢 `in-progress` — actively being worked (a worker is dispatched/live), or a
       worker was on it and the session died (still in-progress, resumable)
     - ⛔ `blocked` — waiting on something external; this **absorbs review-waiting**
       ("waiting on Alison's review") **and paused/parked** ("paused, resume later") —
       the Desc carries which kind it is
     - ✅ `done` — complete

     (Legacy values may still appear in old data — map on display: `active`→`pending`,
     `awaiting-review`→`blocked`, `parked`/`parked=1`→`blocked`, `gone`→`in-progress`.)
   - **Pri** — the numeric priority (`1`/`2`/`3`, blank if null). Lower = higher. Set by
     find-work per the **workspace's configured priority model**; show-work only
     displays it. The tiers' meaning is workspace policy — if you need to gloss what
     `1`/`2`/`3` mean for this workspace, read `pwc sources priority`
     (its `tiers`); absent a config, the generic default is `1` = someone's blocked on
     the user, `2` = active work, `3` = solo/research. Don't hardcode a specific
     workspace's tier definitions into this skill.
   - **ID** — the task id (e.g. `SMT-944`, `slack-...`).
   - **Dir** — where the task's work lives, from the task's `workdir` (in `summary`).
     It's relative to the workspace root, so render it as: a **repo/sub-directory name**
     when set (e.g. `smarta-banking`, `kontax-webapp`); **`/` (root)** when `workdir` is
     empty (`""`) — the work is the workspace root itself, not a sub-repo; and **`—`**
     when it's null/unset (no directory recorded yet — typically a non-code task like a
     Slack reply or a research item). Don't invent a directory the task doesn't carry;
     show `—` rather than guessing.
   - **Desc** — a **short description (≤ ~8 words) that identifies the task**, not a
     restatement of the id. Distil it from the title + latest event so the user can
     recognize the work at a glance (e.g. "mobile OCR fails since 20.05", "review BO
     auth PR #415"). **Every task gets one — never blank.** For a blocked task, make the
     desc say what it's waiting on ("waiting on Maesn", "paused, resume later",
     "waiting on Alison's review"). This column is the point of the briefing — it lets
     the user pick by recognition instead of decoding ids.

   **Sort the main table by status-band in this order**, then by `Pri` (ascending,
   null last) within each band:
   1. 🟢 **in-progress** — work actively in flight (incl. a task whose worker died and
      is resumable).
   2. ⚪ **pending** — ready to start, ordered by priority — the user's "what to pick up".
   3. ⛔ **blocked** — waiting on a person / dependency / review / paused; Desc says
      which.

   `done` is **not** a band in the main table — it goes in the separate Recently-finished
   table described above. (`done` ages off the board on its own after the ~2-day window.)

   **`done` vs. archived — keep them distinct.** `done` means *finished*. **Archived**
   (`archived_at` set) means *off my board but NOT finished* — work that turned out not
   to be mine, got dropped/superseded, or is someone else's ticket I was only tracking.
   Archived tasks **do not appear** in `summary` at all (neither the main table nor the
   Recently-finished table), so you won't render them here; they surface only via
   `summary --archived`. **Never mark something `done` just to get it off the board** —
   if it isn't actually completed, archive it (`pwc archive --task <id> --reason
   "..."`) so its real status is preserved and it doesn't pollute the "just finished"
   recap. Use `done` only for genuinely-completed work.

   Keep each row to one line — this is an index, not a report.

   **Render EVERY row, every time — never truncate or abbreviate.** Show all tasks
   `summary` returns: every active row in the main table AND every `done` row in the
   ~2-day window in the Recently-finished table. Do not shorten the list with "…", a
   "+N more", or a sample — the user asked to see the current tasks, so show all of
   them. If the done list feels long, that's a window-size question (`--done-within-days`),
   not a reason to cut rows from the render.

6. **Summarize the shape of your work** in a sentence or two: how many in-progress,
   how many pending, how many blocked, and anything flagged for attention (an
   in-progress task whose worker died and is resumable, stale tasks). This is the
   orientation the user is actually after.

7. **Offer next moves, don't take them.** End by pointing at what the user might do
   (e.g. "drill into a task, or run `/pwc-pick-work` for a suggestion"). Beyond the worker-status
   and staleness sweeps above (which only detach dead sessions, leaving the task
   in-progress), do not dispatch work or otherwise mutate tasks unless the user asks.

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
- **Done ages off; archiving is separate.** Done tasks stay on the board for ~2 days
  (a "just finished" timeline) then age off `summary` automatically — no step needed.
  For older completed work, `summary --all` shows every non-archived task ever;
  `--done-within-days N` widens or narrows the window. **Archiving** is the distinct,
  explicit act for removing *not-completed* work from the board (not mine / dropped /
  someone else's): `pwc archive --task <id> --reason "..."` sets `archived_at`,
  hides it from `summary`, and **preserves its real status** — so it never masquerades
  as `done`. `summary --archived` lists the archived set; `archive --unarchive` puts a
  task back. Never use `done` as a shortcut to clear the board.
- **Render times in the user's local timezone.** All DB timestamps are stored in
  UTC (the trailing `Z` is literal — `pwc` uses `now_iso()` from `_common.py`
  for everything). When showing a time in the briefing, **convert it to the user's
  local timezone** (e.g. Europe/Berlin → CEST in summer, CET in winter) and tag it
  if there's any room for ambiguity. Showing the raw UTC time and calling it
  "finished 08:57" when their wall clock said 10:57 is a real bug, not a cosmetic
  one — it makes the briefing actively wrong.
