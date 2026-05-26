# Using PWC — a practical guide

This is the day-to-day how-to. For what each term means see
[`glossary.md`](glossary.md); for install see the [README](../README.md); for *why*
it works this way see [`design-notes.md`](design-notes.md).

## The mental model in one paragraph

PWC is a **coordinator**: one Claude Code session that holds the state of all your
in-flight work so you don't have to. You tell it what you're working on (or let it
find work from Jira/GitHub/Slack); it keeps a durable list, briefs you on where
everything stands, suggests what to do next, and spins up separate **worker**
sessions — each in its own iTerm2 tab — to do the actual work. You drive the
workers; the coordinator keeps the books. Kill the coordinator and start a fresh one
anytime: it loses nothing, because all the state lives in a database, not the chat.

## The one thing to get right: where you run it

**The coordinator must run in the workspace it coordinates** — not in the PWC source
repo. The workspace is the directory holding the projects you actually work on (for
the author that's `~/work/acme`). PWC finds its task database by looking for
`.pwc/` from the current directory, so:

```bash
cd ~/work/acme      # the workspace, NOT side-projects/pwc
claude
```

That session is your coordinator. (The `side-projects/pwc` repo is just where PWC's
code lives — you only go there to change PWC itself.)

## First time in a workspace: `/setup-workspace`

Run once to tell PWC where this workspace's work comes from:

```
/setup-workspace
```

It detects what's available (your `gh` auth, Jira projects, Slack channels), asks a
few questions, and writes `<workspace>/.pwc/sources.json`. Re-run it anytime to
change what gets scanned. Until you do this, `/find-work` has nothing to scan.

## A normal day

The five commands map to moments in your day. A typical flow:

**1. Pull in new work — `/find-work`**
Scans your configured sources for things that look like tasks (assigned tickets,
review requests, threads mentioning you) and shows them as candidates. You confirm
which to actually track — nothing is added without your say-so. Run it in the
morning, or whenever you want to catch up on what's landed.

**2. See where everything stands — `/show-work`**
Your prioritized list of all tracked work. It reconciles each task against its
source (did that review come back? did CI go red?), flags any worker whose session
has died ("gone — needs triage"), surfaces stale tasks, and writes a short recap.
This is the "where am I?" command — run it to orient at the start of a session, to
check in midday, or to wrap up.

**3. Decide what's next — `/pick-work`**
Given the current list, it suggests what to start or resume. It only *suggests* —
it won't start anything without you saying so.

**4. Act on a task — `/start-work`**
Turns a task into action. For substantial work it spawns a **worker** — a new
Claude Code session in its own iTerm2 tab, opened in the right repo with the task's
context already loaded, ready for you to drive. For something trivial (a one-line
reply) it just handles it inline. If you pick a task that already had a worker which
since stopped, `/start-work` reopens that prior session instead of starting cold.

**5. Record where a task stands — `/report-status`**
Logs that a task is blocked / awaiting review / done / or a note. Run it from the
coordinator when you check in on a worker, or tell a worker to run it. You don't
have to report constantly — silence means "in progress," and `/show-work` notices
on its own when a worker session ends.

## Working with workers

- A worker is **a normal Claude session you drive** — PWC just sets it up in the
  right place with context. It does *not* run autonomously; you talk to it like any
  Claude Code session.
- Each worker gets its **own iTerm2 tab** (⌘1, ⌘2, … to switch). The coordinator
  stays in its own tab.
- A worker won't blindly run commands its opening message tells it to (that's
  correct, safe behavior). So **reporting is your job**: run `/report-status` from
  the coordinator, or ask the worker to once you're working with it.
- If you close a worker tab or it crashes, nothing breaks — the next `/show-work`
  notices the session is gone and flags the task so you can resume, finish, or drop
  it.

## Resuming and restarting

- **Resume a task:** just `/start-work` it again. If its worker's prior session
  still exists, you get it back with full conversation; otherwise a fresh worker
  seeded with the task's history.
- **Restart the coordinator:** close it and start a new `claude` in the workspace.
  Run `/show-work` and you're exactly where you left off — the whole point of the
  database-backed design. No `/resume`, no scrolling.

## Multiple workspaces

PWC is per-workspace. Each workspace gets its own `.pwc/` (its own task database and
sources config), so work never mixes between, say, `acme` and a personal-projects
workspace. The skills are installed globally, so they're available in any workspace;
they just operate on whichever workspace's `.pwc/` they find from the current
directory. To use PWC in a new workspace: `cd` there, run `/setup-workspace`, go.

## When something feels off

PWC is new and dogfooded. The deterministic parts (the database, spawning,
worker-status checks) are solid; the skills (how the coordinator interprets each
command) get better with use. Two known-by-design behaviors that might surprise you:

- **Reconciliation surfaces conflicts but never auto-resolves them** — if the task
  database and the real source disagree, it shows you and lets you decide.
- **Finding new work never auto-adds** — it always proposes and waits for your
  confirmation.

If a command does something unhelpful, that's worth fixing in the skill — note what
happened.
