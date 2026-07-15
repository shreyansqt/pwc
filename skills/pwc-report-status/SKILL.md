---
name: pwc-report-status
description: Record a status update on a PWC task — blocked, awaiting review, done, or a freeform note. Run from the coordinator (at the workspace root) to log where a task stands, e.g. after checking in on a worker or finishing an inline task.
---

# /pwc-report-status

Record where a task stands into the task database, so the next `/pwc-show-work`
reflects reality — the coordinator reads the task database, it does not watch
worker tabs. This is an append to the event log; it does not change the structured
fields the coordinator owns.

Run this **from the coordinator** to record where a task stands (after checking in
on a worker, or to log the outcome of an inline task you handled). A **worker can
also run it** — PWC's skills are installed globally, so `/pwc-report-status` resolves
in any session. `/pwc-start-work` tells workers to run it only as a closing step, once
they have finished or hit a blocker they cannot clear.

## Configuration

- **CLI**: `pwc` — on PATH (installed by `install.sh` as `~/.local/bin/pwc`). All task-database access goes through it; never read or write the database directly.
- **Workspace**: where the task database lives (`<workspace>/.pwc/taskdb.db`).
  **Auto-discovered from anywhere inside the workspace — you do NOT need
  `--workspace`, from the coordinator or from a worker.** Discovery walks up from the
  current directory and prefers the workspace's `.pwc/` over any nearer `.claude/`, so
  a worker inside a repo subdir (e.g. `…/acme/service-backend`, which has its own
  `.claude/`) still resolves the right db. (This was historically broken — workers had
  to pass `--workspace` — but is fixed; ignore older instructions that say you must.)
  - **Only** if discovery genuinely fails (you're somewhere with no `.pwc` above you)
    pass `--workspace <root>` — and note it's a **global flag: it goes BEFORE the
    subcommand**, e.g. `pwc --workspace <root> log-event …`, never
    `pwc log-event … --workspace <root>` (that errors with "unrecognized
    arguments"). Normally you won't need it at all.
- **Task id**: the task you're recording against (e.g. `SMT-921`) — stated in the
  `/pwc-start-work` seed. If unsure, `/pwc-show-task` resolves it.

## Tools

- `pwc log-event --task <id> --source <src> --kind <kind> --detail "<what>" [--set-status <status>]`
  — the single write path. With `--set-status`, the SAME call also moves the task's
  status field (so `/pwc-show-work` reflects it), not just an event. No `--workspace`
  needed — the db is auto-discovered (see Configuration).

## Steps

1. **Pick the kind** — for the status-bearing ones, *also* pass `--set-status` with
   the same value so the task's status field moves (not just the event log). There are
   exactly **four** statuses: `pending` / `in-progress` / `blocked` / `done`.
   - `in-progress` — you've started and are actively working it.
     → `--set-status in-progress`
   - `blocked` — can't proceed *and you still own the next step once unblocked*:
     waiting on a dependency you'll act on, or you've **paused** it to resume later.
     Put *what* it's waiting on (or "paused, resume later") in `--detail`.
     → `--set-status blocked`
     (There is no separate `awaiting-review` or `parked` status — both are `blocked`;
     the detail line says which.)
   - `done` — **the work is complete from the user's end**, even if a teammate still
     has a downstream step. This is the key call: once the user has done their part —
     a **review approved/changes-requested**, a question answered, a PR sent off, a
     handoff made — the task is `done`, *not* `blocked`. `blocked` is only for when the
     user is still on the hook for the next action; if the remaining step is someone
     else's (their merge, their reply, their deploy), close it `done`. The board is
     "what needs me," so a task the user can't act on shouldn't sit there as `blocked`.
     A `done` task isn't gone — find-work resurfaces it if the teammate comes back
     (and a done task stays on the board ~2 days as a "just finished" line regardless).
     → `--set-status done`
     **Specifically: a PR-review task is `done` the moment the user submits their
     review** (approve or request-changes) — waiting on the author to address comments
     or merge is the author's step, not a `blocked`-on-the-user state.
   - `note` — anything else worth recording (a finding, a direction change). **No
     `--set-status`** — a note doesn't change status.
   (`pending` is the not-started state set at creation; you normally won't set it from
   here — moving *to* pending only makes sense if you're handing a started task back.)

2. **Record it.** Use `--source worker` for a worker's own state, `--source
   coordinator` for an inline outcome you handled. No `--workspace` flag — the db is
   auto-discovered from wherever you are (see Configuration):
   ```
   pwc log-event --task <id> --source <worker|coordinator> \
     --kind <in-progress|blocked|done|note> --detail "<concise description>" \
     [--set-status <in-progress|blocked|done>]
   ```
   The command is `log-event` and the task flag is `--task` (not `list`, not `--ref`).

3. **Keep `--detail` concise and factual** — one line the next brief can read at a
   glance.

4. **Attach every Slack thread the work touched as a working ref.** Whenever you (a
   worker) post to — or meaningfully read — a Slack thread about your task (a status
   post, an ask, a discussion, a decision you're waiting on), attach that thread to the
   task so the find-work tracked-thread sweep can see replies to it later. This is how
   a teammate's answer gets noticed: if the thread isn't on the task, the sweep is
   blind to it and the reply is silently missed (e.g. an approval that lands hours
   later). Do it as part of reporting:
   ```
   pwc add-ref --task <id> --kind working --ref-type slack \
     --value "<thread-permalink>" --label "<channel>: <what this thread is>"
   ```
   **Use the real `thread_ts`** from the message object (`Message_ts` / `thread_ts`
   from `slack_search_*`/`slack_read_*` / the send response) — the permalink's trailing
   `p<digits>` IS that ts with the dot removed (ts `1780312106.321179` →
   `p1780312106321179`). **Never fabricate it** by padding a wall-clock time with zeros;
   a `...000000` suffix resolves to no message and breaks the sweep. Anchor on the
   **parent** message's ts (not a reply's), so the ref covers the whole thread. If a
   thread for this task is already attached, you don't need to re-add it.

4a. **On `done` — mark the outcome on the task's Slack thread(s) with a reaction.**
   The coordinator added 👀 (`eyes`) when it dispatched this task (see
   `/pwc-start-work`), signalling "picked up." Now that you're finished, add the
   **outcome** emoji to the same thread root(s) so the teammate who posted the ask sees
   how it landed, without reading a reply. Pick the emoji from what actually happened:
   - **`white_check_mark`** (✅) — approved / done cleanly / ask satisfied.
   - **`speech_balloon`** (💬) — changes requested / you left review comments to address.
   - **`warning`** (⚠️) — blocked or a problem the teammate needs to act on.

   Choose by the *real* verdict — ✅ on a changes-requested review is a lie; use 💬.
   React on every `slack` ref the task carries (identity + working), parsing
   `channel_id`/`message_ts` from each stored permalink (`/archives/<channel_id>/p<digits>`
   → ts is the digits with a `.` 6 from the end). Use `slack_add_reaction`.

   **Additive, not a swap:** Slack has no remove-reaction API, so the 👀 stays and your
   outcome emoji sits *next to* it — "👀 + ✅" reads correctly as "picked up, then
   approved." Don't try to remove the 👀. **Best-effort:** a failed reaction is skipped,
   not a reason to fail the report. Only applies when the task has Slack refs (skip
   silently otherwise). This step is `done`-only — on `blocked`/`note` you may add ⚠️ if
   it genuinely helps the teammate, but don't mark ✅ on unfinished work.

5. **Attach any playground/investigation artifact you created for this task.** When
   the work produced a dump, CSV, or scratch dir under `_playground/` (see the
   workspace convention), attach its path as a working ref so it's findable from the
   task later, not just buried on disk:
   ```
   pwc add-ref --task <id> --kind working --ref-type file \
     --value "<absolute-path>" --label "<what this artifact is>"
   ```
   Prefer the investigation's directory (the `_playground/<YYYY-MM>/<key>-<slug>/`
   leaf) over each file. If it's already attached, don't re-add it.

6. **If this task used the no-skill fallback gate, propose the skill gap at wrap-up.**
   When `/pwc-start-work` said no skill was configured for this task type, include a
   short skill/instruction review before you finish reporting: say whether the workflow
   looks repeatable and should become a reusable PWC skill or instruction, or whether it
   was a one-off. Do not edit or publish skills from here; surface the recommendation
   for the user to decide.

7. **On `done` only — measure what it cost, and rate the model that ran it.** This is
   the feedback loop that makes routing get *better* instead of staying a guess.

   **a. Measure.** Run `pwc cost --task <id>`. This re-reads the worker session's
   actual token usage from its harness's own store and prices it against the model
   table. Report the figure in one line. Two things to say correctly rather than
   glibly:
   - For **claude/codex** the number is *fair-value at rack rate* — those tokens ran
     on a subscription, so it is what they WOULD have cost on the open market, not
     money billed. That is precisely the number that answers "would a cheaper plan
     cover me?", so don't round it away.
   - For **opencode/OpenRouter** it is real metered spend.
   If it returns `cost_usd: null`, say why (an inline task has no session; a pruned
   transcript can't be read) rather than reporting zero — **a fabricated zero is worse
   than a missing number**, because it silently understates the very bill this exists
   to measure.

   Cost is **re-read live, never frozen at close**: a worker session keeps spending
   after its task is done (the follow-up skill tweak, the docs pass). Re-running
   `pwc cost --task <id>` later always returns the up-to-date total, so don't treat
   the close-out figure as final.

   **b. Rate the routing.** Ask the user one question: *was that model right for this
   task?* — too weak / about right / overkill, plus a one-liner. If they say too weak
   or overkill, write the correction into the table's overlay:
   ```
   pwc models set-tier --key <model-key> --domain <the task's domain> \
     --tier <1-5> --note "<the one-liner>"
   ```
   Lower the tier when a model underperformed (it will stop winning that kind of
   work); raise it when a cheap model did great (it will start winning more). The
   correction lands in the **overlay**, so the next `pwc models fetch` cannot revert
   it — the table becomes calibrated to this user's real experience rather than to
   vendor benchmarks.

   Then log it so the decision is auditable:
   ```
   pwc log-event --task <id> --kind routing-outcome \
     --detail "<harness>/<model>: <rating> — <one-liner> (\$<cost>)"
   ```

   **Skip this whole step for a task with no worker** (inline, never dispatched) —
   there's nothing to measure and nothing to rate.

## Notes

- `--set-status` updates the status field in the same transaction; `note` is
  event-only. Marking a task `done` is all that's needed — there is no archive step;
  a done task ages off the `/pwc-show-work` board on its own after ~2 days.
- You don't need to record "still working" — silence means in progress. If a worker
  session ends without a final report, `/pwc-show-work`'s worker-status check notices
  and flags the task for triage anyway.
