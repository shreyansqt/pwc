# Glossary

The PWC vocabulary, in one place. Terms are grouped by what kind of thing they are.

## Actors

**Coordinator** — The single Claude Code session you talk to. It holds all your
in-flight work, briefs you, suggests what's next, and starts workers. Runs in the
workspace root and deliberately stays *light* — it orchestrates, it doesn't do the
task work itself. Stateless in conversation: kill it and start a fresh one anytime
with zero loss, because everything important lives in the task database.

**Worker** — A *separate* Claude Code session the coordinator starts to do one
task's actual work (the coding, the investigation). Runs in its own iTerm2 **tab**,
in the relevant repo. Bound to one task. Reports status back to the task database.

## Places

**Workspace** — A top-level directory that is one coherent body of work, containing
many repos (e.g. `~/work/acme`). PWC is installed *per workspace*;
each gets its own task database. The coordinator runs here.

**Repo** — One git repo inside a workspace (`service-backend`, `service-banking`, …).
A worker is started in the repo its task concerns (or the workspace root).

**Source repo** — Where PWC's own code lives (`side-projects/pwc`), distinct from
where it *runs* (a workspace). Installed into a workspace via symlinks.

**Task database** — The SQLite file at `<workspace>/.pwc/taskdb.db`. The single
source of truth for all your tasks. "The task database wins" = if your memory (or
the conversation) disagrees with it, the database is right. Accessed only through
`taskdb.py`, never edited by hand.

**Sources config** — The JSON file at `<workspace>/.pwc/sources.json` declaring
which external sources `/find-work` scans for this workspace and how (which Jira
project + JQL, which GitHub org, which Slack channels, …). Written by
`/setup-workspace`, read by `/find-work`. Per-workspace, so different workspaces
draw work from different places.

## Data (what's in the task database — three tables)

**Task** — One unit of work the coordinator tracks (a Jira ticket, a PR review, a
Slack reply, a doc). Has a **meaningful id** derived from its source — a Jira key
(`SMT-874`) or a `<source>-<slug>` (`slack-deploy-window`) — set per the workspace's
id conventions and frozen at creation. A task that later gains a Jira key can be
*promoted* to it, keeping the old id as an **alias** so prior references still
resolve. Also has a **status** (active, blocked, awaiting-review, done, gone, …).

**Alias** — A former id a task was known by, kept after a promotion so old
references (events, your memory, a seeded worker) still resolve to the current id.

**Reference** — An external handle a task carries (`task_refs` table). Two kinds:
*identity* references (Jira key, PR URL, Slack ids) used to recognize "is this the
same task?", and *working-context* references (repo, branch) used to start a worker
in the right place. A task accrues references over its life.

**Event** — An append-only log entry on a task (created, dispatched, status,
blocked, done, …). A task's events *are* its timeline/narrative. Also the source
the **recap** draws on. Kinds: `created | dispatched | status | blocked |
awaiting-review | done | reconcile | new-task | stale-flag | recap | archived |
gone | note`.

## Commands

**`/setup-workspace`** — One-time (per workspace) onboarding: figures out which
external sources of work apply here and how to query each, then writes the
**sources config**. Run again anytime to change what `/find-work` scans.

**`/find-work`** — Explores the configured external sources (Jira, GitHub, Slack,
email) for items that might be new tasks, and queues the ones you confirm. The
*inbound* edge: brings new work in. Surfaces candidates only — never adds a task
without your say-so. (Reads the sources config; run `/setup-workspace` first.)

**`/show-work`** — Reports on the work you're *already tracking*: reads the task
database, reconciles each task against its source, sweeps for dead workers and stale
tasks, recaps, and renders a prioritized view. Run it anytime — morning to orient,
midday to check in, close to wrap up. (Finding *new* work is `/find-work`.)

**`/pick-work`** — Suggests what to work on or resume next. Suggests only; never starts
work on its own.

**`/start-work`** — Turns a task into action: either starts a **worker** (the default,
for substantial work) or handles it **inline** (for trivial work). Also covers
resuming a stopped task by reopening its prior session.

**`/report-status`** — Used *by a worker* to report status (blocked / awaiting-review /
done / note) back to the task database. (Workers usually run the underlying
`taskdb.py log-event` command directly, since the skill isn't on their path from a
repo — see the dispatch notes.)

## Behaviors / mechanisms (mostly inside `/show-work`)

**Inline (handling)** — The coordinator doing a trivial task *itself* in its own
session instead of starting a worker. Reserved for quick things (a one-line reply).

**Reconciliation** — `/show-work` re-checking each task against its external source
(Jira / GitHub / Slack / email) and *surfacing* disagreements. Rule: surface, never
auto-resolve.

**New tasks (noticing)** — `/show-work` spotting things in your inboxes that might be
new tasks and asking whether to add each. Never auto-adds.

**Worker-status check** — Checking whether a worker is *actually still running*
(via `pgrep` on its session id). A worker that's no longer running gets its task
flagged **"gone — needs triage."** Detects death, not outcome — a gone worker may
have left finished-but-unpushed work, so the coordinator never assumes done/failed.
(Distinct from a task's *status* field.)

**Staleness (sweep)** — Flagging active tasks untouched too long. A signal, not a
verdict — surfaces for keep/drop, never auto-archives. Parked tasks are exempt.

**Parked** — A task deliberately waiting on something external (a review, a reply).
Exempt from the staleness sweep; gets a gentler "still waiting?" nudge instead.

**Recap** — `/show-work` summarizing what changed since the last brief, written as one
event. The longitudinal record the build journal and "what did I do this week"
reviews draw on. Also how `/show-work` bounds "since the last brief."

## Worker lifecycle terms

**Session id** — A UUID the coordinator generates *before* starting a worker
(`claude --session-id <uuid>`). Does double duty: it's how `--resume` reopens the
session, and how the worker-status check sees whether it's running (the uuid is in
the worker's command line).

**Resume / resumption** — Reopening a worker's *prior* session (with its full
conversation) rather than starting fresh. No separate command — it's just `/start-work`
reopening an existing session when one survives.
