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
all my tasks in my head.

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
today's email, drafting a design doc. A task may carry one or more *external
references* (Jira key, PR, Slack thread — see the references in
capability 1) or be purely local. Each task has a stable internal ID the
coordinator assigns.

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
start a fresh one at any moment with zero context loss — no resuming the
coordinator's prior session, no scrolling, no reconstruction. Every meaningful
piece of state lives outside the conversation, in a durable local store the
coordinator reads selectively. (Workers are different — dispatch may reopen a
worker's prior session for continuity; it's only the *coordinator* that never
needs resuming.)

The store is a **local SQLite database**. All task database access goes through the
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
  the longitudinal record (what `/show-work`'s recap and the journal draw on)
  comes from an append-only events table inside the database, not from version
  control.

## Core capabilities

1. **Durable task database.** A local SQLite database; single source of truth for
   every active task. Read in two shapes — an always-loaded summary (one status
   line per task) and on-demand per-task detail. A task record includes:
   internal task ID, type, a references (see below), short description,
   status, worker session if spawned, last meaningful event, last touched,
   priority/notes. Persists across coordinator sessions; survives the coordinator
   being killed and restarted.

   **Per-task detail** is three things, not a hand-maintained narrative:
   structured fields (above), a freeform notes section (my private thoughts,
   half-formed ideas), and a task-scoped view of the append-only events table.
   That last one *is* the running narrative — every meaningful event is already
   logged there with a timestamp for the recap, so the per-task timeline
   comes for free as a projection of it rather than something the coordinator
   has to remember to append to separately.

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

2. **Briefing (`/show-work`).** The single interactive surface — run it anytime
   (morning to orient, midday to check inbound, close to roll up), it always
   does the same all-tasks operation. It walks every task in the task database,
   re-checks external state for each against its connected source (Jira, GitHub,
   Slack, email), runs reconciliation (surfacing conflicts — review came back,
   CI went red, worker gone), notices new inbound that looks like a task, sweeps
   for staleness (see below), captures a recap of what's
   happened since the last brief, archives done tasks, and presents a
   prioritized view of all tasks. Answers "where does all my work stand right now?"
   — and produces the same useful briefing whether the coordinator has been
   alive for ten minutes or just booted fresh. v1 keeps this as the *only*
   command deliberately: a lighter inbound-only variant is an obvious later
   split if running the full reconcile several times a day turns out too heavy,
   but that friction should be felt before it's optimized away.

   **Staleness sweep.** End-of-day-style grooming folded in here: the brief
   flags tasks that are nominally active but untouched beyond a configurable
   threshold (default ~7–10 days, no meaningful event) *and* not explicitly
   parked, and asks me to keep or drop each. Like everything else, staleness is
   a signal, not a verdict — the coordinator never auto-archives on age
   (silently deleting work I've stopped watching is the exact failure worker-status check
   detection exists to prevent); it surfaces, I adjudicate. A task that *is*
   explicitly parked
   (blocked, awaiting review) is exempt from the sweep — long parking is a
   gentler, separate nudge ("waiting 14 days — ping them?"), not an archival
   candidate.

3. **Next-action decision (`/pick-work`).** Given current task database state, suggest
   what to start or resume next. Consider blockers, external readiness, and my
   own priorities. Always *suggests*, never auto-dispatches — picking what I
   work on and spawning it without me in the loop would cross from "holds my
   state" into "drives me," and the cost of a wrong auto-pick (a stray worker on
   the wrong task to unwind) dwarfs the one keystroke confirmation costs. A
   trust-it-and-go mode is a plausible later addition, not v1.

4. **Worker dispatch (long-running tasks).** Launch a Claude Code worker for a
   task in its own terminal window. Dispatch makes three decisions from the
   task's task database record:

   - **Directory** — spawns in the task's working directory (from its
     working-context refs), so the worker lands where the code is.
   - **New vs. existing session** — if the task has a prior Claude Code session
     whose transcript still exists on disk (the coordinator recorded its session
     ID at spawn time), dispatch reopens *that* session (`claude --resume`) for
     the richest continuity — the worker comes back with its own full
     conversation intact. Otherwise it starts a fresh session.
   - **Initial prompt** — for a fresh session, the prompt is seeded from the
     task's stored detail and events timeline so the worker continues rather than
     starting cold; for a reopened session, little or no seeding is needed since
     the transcript carries it.

   The coordinator records the worker's session ID in the task database at spawn time
   (by pre-allocating it and passing `--session-id`), so the worker is tracked —
   and reopenable — from the instant it launches. The worker itself writes
   status events ("blocked," "awaiting review," "done") to the task database as it hits
   them; see design notes for why identity is coordinator-assigned but status is
   worker-reported.

   **Dispatch covers resumption — there is no separate resume command.** Picking
   a task back up (a worker that's "gone — needs triage," or any parked task) is
   just dispatching it again, and the new-vs-existing-session choice above is what
   makes that continuous: reopen the prior session when it's still there, else a
   fresh session seeded from durable storage. Two tiers of continuity, prior
   session preferred, our stored detail as the fallback. Re-orientation — "what
   changed externally since I last touched this" — comes from `/show-work`'s per-task
   reconciliation, which already runs on every task, so no dedicated catch-up
   step is needed. A first-time dispatch is just resumption with no prior session
   and empty history.

5. **Inline handling (short-lived tasks).** The coordinator decides inline vs.
   worker at dispatch, and **the default is worker** — inline is reserved for the
   genuinely trivial that can't grow legs (a status check, a one-token reply).
   The bias is deliberate: the failure cost is asymmetric. Worker-when-inline-
   would-do just wastes a spawn — loud and harmless. Inline-when-it-should-have-
   been-a-worker pulls real work into the coordinator's own conversation, which
   is the one thing the architecture exists to prevent (the coordinator must stay
   light and bootable-fresh). Preferring worker protects that context. For the
   tasks that are inlined, the coordinator acts directly via its own skills
   (e.g., `/slack-message`), recording the action and outcome in the task database; if
   an inline task surprises it by growing, it can spin the remainder out into a
   worker rather than absorbing it.

6. **Worker status tracking.** Workers update the task database as they hit meaningful
   events (blocked, awaiting review, sent, done). Coordinator surfaces these in
   the next briefing. When a worker reports "blocked on X," the coordinator just
   records it — no proactive unblocking (chasing a review, pinging a person) on
   its own; that's the same overreach as auto-dispatch. The blocker still
   surfaces naturally whenever a later `/show-work` re-checks that task's external
   state.

   **worker-status check.** Self-reporting only covers workers healthy enough to
   report; a worker that crashed, was force-killed, or had its terminal closed
   reports nothing and would otherwise read "in progress" forever — the task database
   lying about exactly the work I've stopped watching. So at briefing time the
   coordinator checks whether each supposedly-alive worker's session is still
   actually running (testing the stored session/process handle, not a label),
   and for any that aren't, sets status to **"gone — needs triage."** This is
   detection of death, not of outcome: a gone worker may have left finished,
   unpushed work behind, so the coordinator never infers done/failed — it
   surfaces the last known state and I adjudicate (resume, mark done, drop).
   This stays within the on-demand model: worker-status check is evaluated at `/show-work`,
   not by a background daemon.

## Out of scope for v1

- "Needs attention" auto-detection from worker *output* (workers self-report
  meaningful events instead; note this is distinct from worker-status check,
  which the coordinator does do — see capability 6).
- Dynamic parallelism caps.
- A graphical UI — the coordinator lives in a terminal window.
- Cross-machine sync.
- Auto-promoting inbound items into tasks without my confirmation.
- Background polling — sources are checked on-demand via `/show-work`.
- Multi-user / coworker-to-coworker coordination (deliberately deferred; see
  design notes for forward-compatibility hygiene).

## Success criteria

1. I stop using a mental model or external notes to track what's in flight; the
   task database is the truth.
2. Morning ramp-up time drops noticeably — I read a briefing instead of
   reconstructing.
3. Resumption of a paused task feels like continuation, not cold-start.
4. Tasks without external IDs feel as first-class as ticketed work.
5. I can kill the coordinator and start a fresh one mid-day with no loss of
   orientation.
6. After a week of use, I'd be unhappy to give it up.

## Open questions

Deliberately deferred to build time — designed for, but not specified, because
the real cases are best understood when hit rather than guessed at now:

- **Reconciliation conflict rules.** When external state and the task database disagree
  in the overlap (task database says active worker / Jira says done; task database says
  awaiting review / GitHub says approved; CI went red since last touched), the
  rule is "surface, never auto-resolve." The exact set of conflict shapes and
  how each is presented is left for when they show up in practice.
- **Inbound matching.** Deciding whether an inbound item is *new* or an *update*
  to a task already tracked (same Slack thread vs. a new thread mentioning the
  same ticket). The data model captures the raw identity refs needed to solve
  this from day one; the matching logic itself is deferred. Getting it wrong
  silently creates duplicates or swallows new items, so it's surfaced for
  confirmation rather than resolved automatically.
