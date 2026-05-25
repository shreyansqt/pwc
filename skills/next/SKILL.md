---
name: next
description: Suggest what PWC task to start or resume next, given the current ledger state. Considers blockers, external readiness, and priority. Always suggests — never dispatches on its own.
---

# /next

Given the current portfolio, suggest what the user should pick up next. This is a
recommendation, not an action: `/next` never spawns a worker or mutates the ledger.
Picking what to work on and starting it without the user in the loop would cross
from "holds my state" into "drives me" — so `/next` proposes, and the user decides
(and explicitly confirms) before anything is dispatched.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: the current directory; ledger auto-discovered at `<workspace>/.pwc/ledger.db`.

## Tools

- `python3 $SCRIPTS/ledger.py summary` — the portfolio to choose from.
- `python3 $SCRIPTS/ledger.py detail --task <id>` — pull detail only for the one or
  two candidates worth weighing closely (blockers, last events).

## Steps

1. **Load the portfolio** with `ledger.py summary`.

2. **Rank candidates.** Favor, roughly in this order:
   - Tasks flagged for attention — `gone` (a worker needs triage) or `blocked`
     whose blocker may now be clear.
   - High-priority active work (lower `priority` number first).
   - Tasks that are externally *ready* — e.g. a review came back, CI is green — if
     the last `/brief` surfaced that. Pull `detail` to check recent events.
   Deprioritize parked tasks (they're waiting on something) and tasks whose
   blocker clearly hasn't moved.

3. **Suggest one, with a short why** — and optionally a ranked runner-up or two.
   Explain the reasoning in a sentence ("t_0002's review just landed, so it's
   unblocked and it's your highest-priority open item").

4. **Offer to dispatch — then stop.** End by offering to start the suggested task
   (which hands off to the dispatch skill on the user's confirmation). Do not call
   `spawn.py`, do not modify the ledger, do not start anything yourself. Wait for
   an explicit "yes."

## Notes

- `/next` is read-only. The only thing it produces is a suggestion and an offer.
- If the portfolio is empty or everything is parked/blocked, say so plainly rather
  than inventing a candidate.
