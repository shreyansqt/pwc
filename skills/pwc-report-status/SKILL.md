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
  - From the **coordinator** (running at the workspace root), it's auto-discovered —
    no flag needed.
  - From a **worker**, you are inside a *repo under* the workspace (e.g.
    `…/acme/service-banking`), so auto-discovery walking *up* never finds the
    workspace's `.pwc/`. **You MUST pass `--workspace <workspace-root>` explicitly**
    (e.g. `--workspace ~/work/acme`). Without it the command reads the
    wrong/empty db and your report silently goes nowhere.
- **Task id**: the task you're recording against (e.g. `SMT-921`) — stated in the
  `/pwc-start-work` seed. If unsure, `/pwc-show-task` resolves it.

## Tools

- `python3 $SCRIPTS/taskdb.py log-event --task <id> --source <src> --kind <kind> --detail "<what>" [--set-status <status>] [--workspace <root>]`
  — the single write path. With `--set-status`, the SAME call also moves the task's
  status field (so `/pwc-show-work` reflects it), not just an event.

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
   coordinator` for an inline outcome you handled. **From a worker, include
   `--workspace <root>`** (see Configuration):
   ```
   python3 $SCRIPTS/taskdb.py log-event --task <id> --source <worker|coordinator> \
     --kind <blocked|awaiting-review|done|note> --detail "<concise description>" \
     [--set-status <blocked|awaiting-review|done>] [--workspace <workspace-root>]
   ```
   The command is `log-event` and the task flag is `--task` (not `list`, not `--ref`).

3. **Keep `--detail` concise and factual** — one line the next brief can read at a
   glance.

## Notes

- `--set-status` updates the status field in the same transaction; `note` is
  event-only. Marking a task `done` is all that's needed — there is no archive step;
  a done task ages off the `/pwc-show-work` board on its own after ~2 days.
- You don't need to record "still working" — silence means in progress. If a worker
  session ends without a final report, `/pwc-show-work`'s worker-status check notices
  and flags the task for triage anyway.
