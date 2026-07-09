---
name: pwc-pick-work
description: Suggest what PWC task to start or resume next, given the current task database state. Ranks by the priority tier set by find-work (per the workspace's configured priority model, read via `pwc sources priority`), then external readiness. Always suggests — never starts work on its own.
---

# /pwc-pick-work

Given the current set of tasks, suggest what the user should pick up next. This is a
recommendation, not an action: `/next` never spawns a worker or mutates the task database.
Picking what to work on and starting it without the user in the loop would cross
from "holds my state" into "drives me" — so `/next` proposes, and the user decides
(and explicitly confirms) before anything is started.

## Configuration

- **CLI**: `pwc` — on PATH (installed by `install.sh` as `~/.local/bin/pwc`). All task-database access goes through it; never read or write the database directly.
- **Workspace**: the current directory; task database auto-discovered at `<workspace>/.pwc/taskdb.db`.

## Tools

- `pwc summary` — the tasks to choose from.
- `pwc detail --task <id>` — pull detail only for the one or
  two candidates worth weighing closely (blockers, last events).

## Steps

1. **Load all tasks** with `pwc summary`.

2. **Rank candidates.** Favor, roughly in this order:
   - Tasks flagged for attention — an `in-progress` task whose worker died (resumable,
     pick it back up) or a `blocked` task whose blocker may now be clear. Then
     `pending` work ready to start.
   - **Priority** (lower = higher; null sorts last) is the dominant ranking signal, and
     it's already been set by find-work per the **workspace's configured priority model**.
     `summary` returns rows in priority order, so the top `1` usually *is* the answer.
     To *explain* a suggestion in the workspace's own terms (what `1`/`2`/`3` mean here),
     read the model with `pwc sources priority` and phrase the "why"
     using its `tiers`. If it returns `{}` (no model configured), treat the generic
     default — `1` = someone's blocked on the user, `2` = active work, `3` = solo — and
     within `1`, lean toward the blocking-others kind first (a teammate idling on the
     user's review costs more than the user's own queue depth). Don't re-derive or
     override the numeric priority here — that's find-work's job; pick-work only ranks
     and explains.
   - Within the same priority, tasks that are externally *ready* — e.g. a review came
     back, CI is green — as last surfaced by `/pwc-show-work`. Pull `detail` to read
     the recent events (a `note` like "blocks Stella: re-review #690" tells you who's
     waiting and why).
   Deprioritize parked tasks (they're waiting on something) and tasks whose blocker
   clearly hasn't moved. If priorities are all null (find-work hasn't tiered them
   yet), fall back to reading each candidate's events/refs to judge who's waiting.

3. **Suggest one, with a short why** — and optionally a ranked runner-up or two.
   Explain the reasoning in a sentence ("t_0002's review just landed, so it's
   unblocked and it's your highest-priority open item").

4. **Offer to start it — then stop.** End by offering to start the suggested task
   (which hands off to the /pwc-start-work skill on the user's confirmation). Do not call
   `pwc spawn`, do not modify the task database, do not start anything yourself. Wait for
   an explicit "yes."

## Notes

- `/next` is read-only. The only thing it produces is a suggestion and an offer.
- If there are no tasks, or everything is parked/blocked, say so plainly rather
  than inventing a candidate.
