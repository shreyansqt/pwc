---
name: pwc-triage
description: Clear the rot off a PWC board. Checks every not-done task against REALITY (was the PR merged? was it never opened? has the repo moved on without it? has nothing happened in months?), then presents a two-tier digest — a batch of obvious calls you approve in one go, plus the genuinely ambiguous ones one at a time. Use when a board has drifted, when tasks are piling up stale, or when picking a neglected workspace back up.
---

# /pwc-triage

A board only helps if you trust it. The moment it carries tasks that are finished,
abandoned, or superseded, you stop reading it — and then it carries *everything*
badly. `/pwc-triage` is the garbage collection: go through every task that isn't
`done` or archived, work out what actually happened to it, and close the books.

**This skill's whole value is the homework.** Anyone can ask you nineteen questions.
The point is to *not* have to: most stale tasks have a knowable fate sitting in
GitHub, in the repo's git log, or in the task's own event history. Find it first,
then ask the user only what genuinely needs a human.

## Configuration

- **CLI**: `pwc` — on PATH. All task-database access goes through it.
- **Workspace**: the current directory. From a parent of several workspaces, triage
  **one at a time** — pass `--workspace <dir>` and say which board you're on. A
  combined triage would mix boards whose priorities mean different things.

## Tools

- `pwc summary` — the board. Triage covers every row that is **not** `done` (pending,
  in-progress, blocked). Archived tasks are already off the board; leave them.
- `pwc detail --task <id>` — the task's refs and event timeline. **This is the primary
  evidence.** Its refs point at the PRs, branches, and threads whose current state
  decides the task's fate.
- `pwc worker-status --session-ids <id>` — is a task claiming `in-progress` actually
  being worked on, or did its worker die weeks ago?
- `gh pr view` / `gh pr list` / `git log` — the outside world, which is where the
  answer usually is.
- `pwc update-task` / `pwc archive` / `pwc log-event` — the actions. **Never taken
  without the user's approval.**

## Steps

1. **Load the board and age it.** `pwc summary`, keep everything not `done`. For each,
   compute days since `last_event_at`. Age alone is not a verdict — it's the signal
   that tells you where to spend the homework.

2. **Do the homework — this is the step that matters.** For each task, find out what
   actually happened. Do not ask the user anything you could have looked up.

   - **Its refs are the evidence.** `pwc detail` gives the PRs, branches, threads.
     Check each one's *current* state: a PR ref that is now `MERGED` means the task is
     almost certainly **done**. A PR that is `CLOSED` unmerged means it was
     **abandoned**. A branch that no longer exists means the work landed or was
     dropped.
   - **A task naming a PR that was never opened is a plan, not work in flight.** (Real
     case: five tasks named "PR 3", "PR 4" … "PR 7" of a planned series. PRs 1 and 2
     merged; 3-7 were never opened, and the repo moved on without them. They weren't
     *blocked* — the plan was simply abandoned.)
   - **Check whether the repo moved on without the board.** `git log` the task's
     `workdir`. If a project has commits from last week but PWC thinks its tasks are a
     month stale, **the work is happening outside PWC** — that's the finding, and it
     matters more than any individual task's status. Say so explicitly.
   - **`in-progress` + a dead worker + weeks of silence is not in-progress.** Run
     `worker-status`. A task whose worker died a month ago is `pending` (resumable) at
     best — it is not being worked on, and leaving it green is a lie that makes the
     board unreadable.
   - **`blocked` needs a named blocker.** Read the event history: what is it waiting
     on, and is that still true? A task blocked on something that resolved weeks ago
     isn't blocked. A task blocked on nothing anyone can name is abandoned.

3. **Sort into two tiers.** Everything must land in one of them — a task you couldn't
   decide about is a `NEEDS YOU`, not a silent skip.

   **Tier 1 — the safe batch: evidence decides it, one approval covers them all.**
   These are the calls where the homework produced an unambiguous answer and the
   action is reversible:
   - Task's PR merged → **close as `done`**.
   - Task's PR closed unmerged / branch gone / superseded → **archive** (off the board,
     status preserved — never fake it as `done`).
   - `in-progress` with a worker dead for weeks → **reset to `pending`** (do NOT clear
     the session id; it's the only handle on the transcript).
   - Obvious duplicates of another task → **merge** (`pwc merge`), naming the survivor.

   **Tier 2 — needs you: the homework ran out.** One at a time, with `AskUserQuestion`,
   and each one carries **what you found** plus **a recommendation**, never a bare
   "what should I do with this?". The user is answering a question you've already done
   the work on:
   - Is this still wanted at all? (a plan that went stale — no PR, no commits, no
     evidence either way)
   - Is this blocker still real?
   - Is this still the right priority now that N weeks have passed?

4. **Present the digest, then act on approval.** Show tier 1 as a list with the
   evidence for each line (`close  shreyans-co-18..22 — PRs never opened; repo moved on
   (PR #25 merged 12.06)`), and ask for **one approval for the batch**. Then walk tier
   2. **Nothing mutates before the user says so** — this is PWC's standing rule
   (surface, never auto-promote), and it applies here with force: triage is the one
   skill whose job is *deleting things off the board*.

   The user may veto any line in the batch; take the batch minus the vetoes.

5. **Log why, not just what.** Every close/archive gets a `log-event` recording the
   *evidence* ("PR #24 merged 2026-06-12", "never opened; superseded by #25"), not just
   "triaged". A year from now the question will be *why did I drop this*, and the
   answer has to be in the task, not in a chat log that's gone.

6. **Report what the board looked like before and after**, and surface any
   **structural** finding the homework turned up — those are worth more than the
   individual closes. ("breezemail has commits from 8 days ago but PWC hasn't seen an
   event in 31 — the work is happening outside the board entirely.")

## Notes

- **Triage is not the staleness sweep.** `/pwc-show-work` *flags* stale tasks in
  passing; `/pwc-triage` is the deliberate pass that resolves them. Run show-work
  daily, triage when a board has rotted.
- **`done` vs archived, strictly.** `done` means finished. **Archived** means off the
  board but NOT finished (abandoned, superseded, turned out not to be yours). Never
  mark something `done` just to clear it — that pollutes the "just finished" recap and
  lies about your history. If it wasn't completed, archive it with a reason.
- **Never clear a session id to "clean up".** It is the only pointer to the worker's
  transcript — the task's resumability and its cost measurement both hang off it. A
  dead worker is not a gone session.
- **An unpaid board rots differently from a paid one.** A work board is groomed by
  external pressure (tickets, reviewers, standups); a personal board has none of that,
  so it accumulates plans that were never abandoned *out loud*. Expect triage on a
  side-project board to be mostly "this was a plan I stopped believing in" — and that
  is a perfectly good reason to archive, worth recording as such.
