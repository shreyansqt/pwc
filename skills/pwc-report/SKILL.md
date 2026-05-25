---
name: pwc-report
description: For a PWC worker to report its status back to the coordinator's ledger — blocked, awaiting review, done, or a freeform note. Run this when you hit a meaningful event in the task you were dispatched to work on.
---

# /pwc-report

A thin reporting channel for a **worker** session. When PWC dispatches you to a
task, you were told your task id in the opening prompt. As you hit meaningful
events, record them here so the coordinator's next `/brief` reflects reality — the
coordinator reads the ledger, it does not watch your window.

This is the worker's only write to the ledger: an append to the event log. It does
not change task fields the coordinator owns.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: your current directory; the ledger is auto-discovered from it.
- **Your task id**: given to you in your opening prompt (e.g. `t_0007`). If you
  don't have one, you weren't dispatched by PWC — don't report.

## Tools

- `python3 $SCRIPTS/ledger.py log-event --task <your-id> --source worker --kind <kind> --detail "<what happened>"`

## Steps

1. **Pick the kind** that matches the event:
   - `blocked` — you can't proceed (waiting on a person, a dependency, a decision).
     Put what you're blocked on in `--detail`.
   - `awaiting-review` — work is up for review (PR opened, sent for feedback).
   - `done` — the task's work is complete.
   - `note` — anything else worth recording (a finding, a direction change).

2. **Report it:**
   ```
   python3 $SCRIPTS/ledger.py log-event --task <your-id> --source worker \
     --kind <blocked|awaiting-review|done|note> --detail "<concise description>"
   ```

3. **Keep `--detail` concise and factual** — one line the coordinator can read at a
   glance in the next brief. Report when state actually changes, not continuously.

## Notes

- Always use `--source worker`. Always use your own task id.
- You report **events**; you do not set task status, archive, or touch other tasks.
  The coordinator owns those. Reporting `done` signals completion — the coordinator
  decides when to archive.
- You don't need to report that you're "still working" — silence means in progress.
  If your session dies without a final report, the coordinator's liveness check will
  notice and flag the task for triage.
