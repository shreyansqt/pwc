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
in any session — but only once it's warmed up and *you've asked it to*; a fresh
worker correctly won't run a reporting command just because its opening message
said so, and the `/pwc-start-work` seed deliberately doesn't ask. So in practice
reporting is human-initiated: you run it from the coordinator, or you tell the
worker to.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: where the task database lives (`<workspace>/.pwc/taskdb.db`).
  **Auto-discovered from anywhere inside the workspace — you do NOT need
  `--workspace`, from the coordinator or from a worker.** Discovery walks up from the
  current directory and prefers the workspace's `.pwc/` over any nearer `.claude/`, so
  a worker inside a repo subdir (e.g. `…/acme/service-backend`, which has its own
  `.claude/`) still resolves the right db. (This was historically broken — workers had
  to pass `--workspace` — but is fixed; ignore older instructions that say you must.)
  - **Only** if discovery genuinely fails (you're somewhere with no `.pwc` above you)
    pass `--workspace <root>` — and note it's a **global flag: it goes BEFORE the
    subcommand**, e.g. `taskdb.py --workspace <root> log-event …`, never
    `taskdb.py log-event … --workspace <root>` (that errors with "unrecognized
    arguments"). Normally you won't need it at all.
- **Task id**: the task you're recording against (e.g. `SMT-921`) — stated in the
  `/pwc-start-work` seed. If unsure, `/pwc-show-task` resolves it.

## Tools

- `python3 $SCRIPTS/taskdb.py log-event --task <id> --source <src> --kind <kind> --detail "<what>" [--set-status <status>]`
  — the single write path. With `--set-status`, the SAME call also moves the task's
  status field (so `/pwc-show-work` reflects it), not just an event. No `--workspace`
  needed — the db is auto-discovered (see Configuration).

## Steps

1. **Pick the kind** — for the status-bearing ones, *also* pass `--set-status` with
   the same value so the task's status field moves (not just the event log):
   - `blocked` — can't proceed (waiting on a person, dependency, decision). Put what
     it's blocked on in `--detail`. → `--set-status blocked`
   - `awaiting-review` — work is up for review (PR opened / sent for feedback).
     → `--set-status awaiting-review`
   - `done` — the task's work is complete. → `--set-status done`
   - `note` — anything else worth recording (a finding, a direction change). **No
     `--set-status`** — a note doesn't change status.

2. **Record it.** Use `--source worker` for a worker's own state, `--source
   coordinator` for an inline outcome you handled. No `--workspace` flag — the db is
   auto-discovered from wherever you are (see Configuration):
   ```
   python3 $SCRIPTS/taskdb.py log-event --task <id> --source <worker|coordinator> \
     --kind <blocked|awaiting-review|done|note> --detail "<concise description>" \
     [--set-status <blocked|awaiting-review|done>]
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
   python3 $SCRIPTS/taskdb.py add-ref --task <id> --kind working --ref-type slack \
     --value "<thread-permalink>" --label "<channel>: <what this thread is>"
   ```
   **Use the real `thread_ts`** from the message object (`Message_ts` / `thread_ts`
   from `slack_search_*`/`slack_read_*` / the send response) — the permalink's trailing
   `p<digits>` IS that ts with the dot removed (ts `1780312106.321179` →
   `p1780312106321179`). **Never fabricate it** by padding a wall-clock time with zeros;
   a `...000000` suffix resolves to no message and breaks the sweep. Anchor on the
   **parent** message's ts (not a reply's), so the ref covers the whole thread. If a
   thread for this task is already attached, you don't need to re-add it.

## Notes

- `--set-status` updates the status field in the same transaction; `note` is
  event-only. Marking a task `done` is all that's needed — there is no archive step;
  a done task ages off the `/pwc-show-work` board on its own after ~2 days.
- You don't need to record "still working" — silence means in progress. If a worker
  session ends without a final report, `/pwc-show-work`'s worker-status check notices
  and flags the task for triage anyway.
