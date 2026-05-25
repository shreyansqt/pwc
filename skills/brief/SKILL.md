---
name: brief
description: The PWC coordinator's whole-portfolio briefing. Reads the durable ledger and presents a prioritized view of all in-flight work. Run it anytime — morning to orient, midday to check in, close to wrap up. This is the first thing to run after starting (or restarting) the coordinator.
---

# /brief

The single interactive surface of the PWC coordinator. It reads the durable
SQLite ledger and renders a prioritized portfolio of everything in flight, so the
user never has to hold the state of their work in their head. It produces the same
useful briefing whether the coordinator booted ten minutes ago or just now — all
state lives in the ledger, not in this conversation.

This skill is being built in layers. **Current layer: render-only** (read the
ledger and present it). Later layers add liveness detection, a staleness sweep,
external reconciliation, inbound noticing, and a log rollup — each documented as a
section below when implemented.

## Configuration

- **Scripts directory** (the shared PWC mechanism):
  `~/work/pwc/scripts`
  Referred to below as `$SCRIPTS`. All ledger access goes through these scripts —
  never read or write the database directly.
- **Workspace**: the directory the coordinator is running in. The ledger lives at
  `<workspace>/.pwc/ledger.db` and the scripts discover it automatically from the
  current working directory, so no `--workspace` flag is normally needed.

## Tools

- `python3 $SCRIPTS/ledger.py summary` — the always-loaded index: one status line
  per active task, archived tasks excluded. Returns a JSON array.
- `python3 $SCRIPTS/ledger.py detail --task <id>` — full per-task detail: the
  structured fields, its typed references, and its event timeline. Use only when
  drilling into a specific task, not for the overview.

## Steps

1. **Load the portfolio.** Run `python3 $SCRIPTS/ledger.py summary`. If it errors
   with "no ledger" the workspace isn't initialized — tell the user to run the PWC
   install/init for this workspace and stop.

2. **Render a prioritized briefing** from the summary JSON. Do not dump raw JSON —
   present a scannable view. Group and order so the user can triage at a glance:

   - Lead with anything needing attention: tasks whose `status` is `gone`
     (a worker vanished — needs triage) or `blocked`.
   - Then active work, ordered by `priority` (lower number = higher priority;
     `null` priority sorts last). The `summary` call already returns rows in this
     order — preserve it.
   - Show parked tasks (`parked = 1`) in a separate, quieter group — they're
     waiting on something external and aren't asking for action right now. Note
     their `parked_reason`.

   For each task show: the internal id, type, title, status, and — if it has one —
   a hint that a worker session is attached (`session_id` is non-null). Keep each
   task to roughly one line; this is an index, not a report.

3. **Summarize the shape of the portfolio** in a sentence or two: how many active,
   how many parked, anything flagged for attention. This is the orientation the
   user is actually after.

4. **Offer next moves, don't take them.** End by pointing at what the user might do
   (e.g. "drill into a task, or run `/next` for a suggestion") — but do not dispatch
   work, modify the ledger, or drill into details unless asked. `/brief` is
   read-only in this layer.

## Notes

- **The ledger is the source of truth, not this conversation.** If something here
  disagrees with what you remember from earlier in the session, the ledger wins.
  Never invent tasks or statuses that aren't in the `summary` output.
- **Stay light.** `/brief` should not pull full `detail` for every task — that
  defeats the two-tier design. Load `detail` only for a task the user is focusing on.
- Archived (done-and-rolled-up) tasks are intentionally absent from `summary`. If
  the user asks about completed work, add `--include-archived`.
