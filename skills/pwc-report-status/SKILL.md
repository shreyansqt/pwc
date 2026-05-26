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
- **Workspace**: the coordinator's current directory; task database auto-discovered.
- **Task id**: the task you're recording against (e.g. `t_0007`).

## Tools

- `python3 $SCRIPTS/taskdb.py log-event --task <id> --source worker --kind <kind> --detail "<what happened>"`

## Steps

1. **Pick the kind** that matches the event:
   - `blocked` — can't proceed (waiting on a person, a dependency, a decision).
     Put what it's blocked on in `--detail`.
   - `awaiting-review` — work is up for review (PR opened, sent for feedback).
   - `done` — the task's work is complete.
   - `note` — anything else worth recording (a finding, a direction change).

2. **Record it** (use `--source worker` for a worker's state, `--source coordinator`
   for an inline outcome you handled):
   ```
   python3 $SCRIPTS/taskdb.py log-event --task <id> --source <worker|coordinator> \
     --kind <blocked|awaiting-review|done|note> --detail "<concise description>"
   ```

3. **Keep `--detail` concise and factual** — one line the next brief can read at a
   glance.

## Notes

- This records **events**; it does not archive or change task status fields —
  `/pwc-show-work` handles archiving a confirmed-done task.
- You don't need to record "still working" — silence means in progress. If a worker
  session ends without a final report, `/pwc-show-work`'s worker-status check notices
  and flags the task for triage anyway.
