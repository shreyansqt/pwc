---
name: pwc-report
description: For a PWC worker to report its status back to the coordinator's task database — blocked, awaiting review, done, or a freeform note. Run this when you hit a meaningful event in the task you were started on.
---

# /pwc-report

A thin reporting channel for a worker to record status to the task database. As you hit
meaningful events, record them so the coordinator's next `/brief` reflects
reality — the coordinator reads the task database, it does not watch your window.

This is a worker's only write to the task database: an append to the event log. It does
not change task fields the coordinator owns.

> **Note for spawned workers:** PWC's skills are installed at the *workspace root*,
> but you run in a repo, so this `/pwc-report` skill is usually **not
> resolvable from your cwd**. Your /start prompt therefore gave you the literal
> `taskdb.py log-event ...` command to run directly — use that. This SKILL.md
> documents the same call for when the skill *is* available (e.g. the coordinator
> reporting an inline task's outcome from the workspace root).

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: your current directory; the task database is auto-discovered from it.
- **Your task id**: given to you in your opening prompt (e.g. `t_0007`). If you
  don't have one, you weren't started by PWC — don't report.

## Tools

- `python3 $SCRIPTS/taskdb.py log-event --task <your-id> --source worker --kind <kind> --detail "<what happened>"`

## Steps

1. **Pick the kind** that matches the event:
   - `blocked` — you can't proceed (waiting on a person, a dependency, a decision).
     Put what you're blocked on in `--detail`.
   - `awaiting-review` — work is up for review (PR opened, sent for feedback).
   - `done` — the task's work is complete.
   - `note` — anything else worth recording (a finding, a direction change).

2. **Report it:**
   ```
   python3 $SCRIPTS/taskdb.py log-event --task <your-id> --source worker \
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
  If your session dies without a final report, the coordinator's worker-status check will
  notice and flag the task for triage.
