# PWC — Product Requirements Document (v1)

> Working name: PWC (Personal Work Coordinator). Final name deferred until after
> initial dogfooding.

## Problem

I run multiple Claude Code sessions in parallel across the various tasks that
make up my day — some tracked in Jira, some not (reviewing a PR, replying to a
Slack thread, triaging email, drafting a doc). The agents are productive, but
I'm the bottleneck: I have to remember the status of every in-flight task,
decide what to pick up next, track external dependencies (PR reviews, CI,
replies I'm waiting on), and reconstruct context every time I switch. The
cognitive load of being the coordinator drains me more than the work itself.

## Goal

A personal coordinator agent that holds the state of all my in-flight work for
me, briefs me on it, decides what to do next, and dispatches worker sessions to
do the actual work — so I can focus on driving individual tasks without holding
the portfolio in my head.

## Non-goals

- Not an autonomous end-to-end pipeline. Human stays in the loop on every worker.
- Not a multi-agent collaboration system. Workers don't talk to each other.
- Not a team tool. Single-user, personal.
- Not a replacement for existing skills (`/start-ticket`, `/ship`, `/slack-message`,
  etc.) — it orchestrates them.

## Users

Me. One person, dogfooded daily.

## Core concepts

**Task.** The unit of work the coordinator tracks. Examples: implementing a Jira
ticket, reviewing a specific PR, responding to a Slack thread, processing
today's email, drafting a design doc. A task may have an *external reference*
(Jira key, PR URL, Slack permalink) or be purely local. Each task has a stable
internal ID the coordinator assigns.

**Worker.** A Claude Code session spawned in a separate terminal window to act
on a specific long-running task. Bound to one task at a time. Workers are
spawned only for tasks that warrant their own session — typically anything that
involves real coding, multi-step investigation, or sustained back-and-forth.

**Inline handling.** Short-lived tasks (a one-shot Slack reply, a quick email
triage, a status check) are handled by the coordinator directly without
spawning a worker. The coordinator decides which mode a given task warrants
when it dispatches.

## Memory model

The coordinator must be **stateless in its conversation context, stateful in
its external memory.** I should be able to kill the coordinator session and
start a fresh one at any moment with zero context loss — no `/resume`, no
scrolling, no reconstruction. Every meaningful piece of state lives outside the
conversation, in a durable local store the coordinator reads selectively.

The store is a **local SQLite database**. All ledger access goes through the
coordinator — it is the only reader and writer; there is no expectation of
hand-editing the store directly. SQLite is chosen over plain files for one
decisive reason: state changes that span the two memory tiers (e.g. spawning a
worker updates both the task's detail and its summary line) commit as a single
transaction, so the "kill the coordinator anytime with zero loss" guarantee
holds by construction rather than by a hand-rolled atomic-write scheme.

This means:

- **No reliance on the coordinator's own conversation history** for anything
  important. Anything the coordinator needs to "remember" tomorrow must be
  written to durable memory today.
- **No reliance on the coordinator's own conversation history** for anything
  important. Anything the coordinator needs to "remember" tomorrow must be
  written to durable memory today.
- **Two-tier access, one store.** The two tiers are not two stores but two
  query shapes against the same database: a lightweight **summary** read at the
  start of every session (one line of status per active task), and **per-task
  detail** read on demand only when working on a given task. They cannot drift
  from each other — they are projections of the same rows.
- **Memory is the source of truth, not the conversation.** If the store says a
  task is blocked on review and the conversation thinks otherwise, the store wins.
- **Coordinator boot routine.** On startup, the coordinator reads the summary
  and a small "where I left off" pointer (last activity timestamp, anything
  flagged for follow-up). That's its entire context-restoration step. Fast,
  deterministic, identical every time.
- **Writes are immediate and transactional.** Any state change — task created,
  status updated, worker spawned, task done — commits before the coordinator
  moves on, and multi-tier changes commit atomically. No "I'll save at end of
  session" semantics.
- **History lives in the schema, not in git.** A SQLite file doesn't diff, so
  the longitudinal record (what `/eod` and the journal draw on) comes from an
  append-only events table inside the database, not from version control.

## Core capabilities

1. **Durable work ledger.** A local SQLite database; single source of truth for
   every active task. Read in two shapes — an always-loaded summary (one status
   line per task) and on-demand per-task detail. A task record includes:
   internal task ID, type, a typed reference set (see below), short description,
   status, worker session if spawned, last meaningful event, last touched,
   priority/notes. Persists across coordinator sessions; survives the coordinator
   being killed and restarted.

   **Typed reference set.** A task carries not one external reference but a
   small, typed, multi-valued set that accrues over the task's life (a Jira
   ticket becomes a branch becomes a PR). Two kinds:

   - **Identity refs** — the stable handles used to answer "is this inbound
     thing the same task?": Jira key, Slack channel ID + message timestamp, PR
     URL/number. Store the raw, stable identifier (Slack `channel_id` + `ts`,
     not a channel name or a derived link) — human labels and permalinks can be
     reconstructed from IDs, but not matched against reliably.
   - **Working-context refs** — what a worker needs to act and what resumption
     needs to relocate: local working directory, branch name, clickable PR
     link. These are machine-specific and (unlike identity refs) would not
     survive a future multi-machine world.

2. **Cold-start briefing (`/standup`).** Pulls latest state from connected
   sources (Jira, GitHub, Slack, email), reconciles with the ledger, surfaces
   new things that look like tasks, and presents a prioritized portfolio view.
   Designed to be the *first thing* I run after starting the coordinator — and
   to produce the same useful briefing whether the coordinator has been alive
   for ten minutes or just booted fresh.

3. **Inbound surface (`/whats-new`).** Lighter-weight check during the day —
   what's landed in my inboxes that might be a task, without a full standup. I
   decide whether to promote each one into the ledger.

4. **Next-action decision (`/pick-next`).** Given current ledger state, suggest
   what to start or resume next. Consider blockers, external readiness, and my
   own priorities.

5. **Worker dispatch (long-running tasks).** Spawn a Claude Code worker in a
   new terminal window — in the right working directory, with a context-loaded
   initial prompt. Worker registers itself in the ledger so the coordinator
   knows it's alive.

6. **Inline handling (short-lived tasks).** For tasks the coordinator judges as
   quick, single-shot, or not worth a dedicated worker, the coordinator acts
   directly via its own skills (e.g., `/slack-message`). The action and its
   outcome are still recorded in the ledger.

7. **Worker status tracking.** Workers update the ledger as they hit meaningful
   events (blocked, awaiting review, sent, done). Coordinator surfaces these in
   the next briefing.

   **Liveness detection.** Self-reporting only covers workers healthy enough to
   report; a worker that crashed, was force-killed, or had its terminal closed
   reports nothing and would otherwise read "in progress" forever — the ledger
   lying about exactly the work I've stopped watching. So at briefing time the
   coordinator checks whether each supposedly-alive worker's session is still
   actually running (testing the stored session/process handle, not a label),
   and for any that aren't, sets status to **"gone — needs triage."** This is
   detection of death, not of outcome: a gone worker may have left finished,
   unpushed work behind, so the coordinator never infers done/failed — it
   surfaces the last known state and I adjudicate (resume, mark done, drop).
   This stays within the on-demand model: liveness is evaluated at `/standup`,
   not by a background daemon.

8. **Resumption by task (`/resume <task>`).** Re-attach to an existing worker
   by task — referenced by external ID, internal ID, or fuzzy description.
   Before re-attaching, show me a card with current status and what's changed
   externally since I last touched it. This is *worker* resumption, distinct
   from the coordinator itself, which never needs explicit resumption.

9. **End-of-day rollup (`/eod`).** Walk today's ledger activity, produce a
   longitudinal log entry. Archive done tasks. Updates the durable memory so
   tomorrow's cold-start briefing reflects today's work.

## Out of scope for v1

- "Needs attention" auto-detection from worker *output* (workers self-report
  meaningful events instead; note this is distinct from liveness detection,
  which the coordinator does do — see capability 7).
- Dynamic parallelism caps.
- A graphical UI — the coordinator lives in a terminal window.
- Cross-machine sync.
- Auto-promoting inbound items into tasks without my confirmation.
- Background polling — sources are checked on-demand via `/standup` and `/whats-new`.
- Multi-user / coworker-to-coworker coordination (deliberately deferred; see
  design notes for forward-compatibility hygiene).

## Success criteria

1. I stop using a mental model or external notes to track what's in flight; the
   ledger is the truth.
2. Morning ramp-up time drops noticeably — I read a briefing instead of
   reconstructing.
3. Resumption of a paused task feels like continuation, not cold-start.
4. Tasks without external IDs feel as first-class as ticketed work.
5. I can kill the coordinator and start a fresh one mid-day with no loss of
   orientation.
6. After a week of use, I'd be unhappy to give it up.

## Open questions

- Should `/pick-next` ever auto-dispatch, or always wait for my confirmation?
  (Lean: always confirm in v1.)
- When a worker reports "blocked on X," does the coordinator do anything
  proactive, or just record it? (Lean: just record in v1.)
- How does the coordinator decide inline vs. worker for a given task? Heuristic
  (task type-based), or does it ask me each time? (Lean: heuristic with override
  — e.g., Slack replies always inline, Jira tickets always worker, ambiguous
  cases ask.)
- Should per-task detail files include a running narrative the coordinator
  appends to as things happen, or just structured fields? (Lean: structured
  fields plus a freeform notes section.)
