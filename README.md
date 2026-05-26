# PWC — Personal Work Coordinator

A single-user coordinator that holds the state of all your in-flight work, briefs
you on it, suggests what to do next, and dispatches Claude Code worker sessions to
do the actual work — so you stop being the bottleneck who keeps every task
in your head.

The coordinator **is** a Claude Code session: its capabilities are skills, the
deterministic mechanism is Python scripts, and all state lives in a per-workspace
SQLite task database. Kill the coordinator and start a fresh one anytime — there is no
context to lose, because nothing important lives in the conversation.

**New to PWC? Start with the [user guide](docs/guide.md)** — the practical
how-to-use walkthrough. See also [`docs/prd.md`](docs/prd.md) and
[`docs/design-notes.md`](docs/design-notes.md) for the full design and the
reasoning behind it, and [`docs/glossary.md`](docs/glossary.md) for the vocabulary.

## Layout

```
pwc/                      # this repo — the SOURCE (develop & commit here)
  schema.sql              # the task database schema
  scripts/                # shared deterministic mechanism ("the hands")
    taskdb.py             #   single read/write path to the task database (CLI, JSON I/O)
    spawn.py              #   spawn a worker in an iTerm2 window
    worker_status.py      #   is a worker session still running? (pgrep)
    sources.py            #   per-workspace sources config (read/write/validate)
    claude_md.py          #   splice the PWC section into a workspace CLAUDE.md
    pwc_db.py, _common.py #   shared connection / discovery helpers
  skills/                 # the coordinator's brain (SKILL.md each, pwc- prefixed)
    pwc-setup-workspace/  pwc-find-work/  pwc-show-work/
    pwc-pick-work/  pwc-start-work/  pwc-report-status/
  install.sh              # symlink skills globally + init a workspace's task database
```

Runtime state lives **in each workspace**, never here: `<workspace>/.pwc/taskdb.db`.

## Install

```bash
./install.sh ~/work/acme     # or omit the arg for that default
```

Two parts:

- **Skills go global** — symlinked into `~/.claude/skills/` so *every* Claude Code
  session sees them, in any directory. This matters because a spawned worker runs
  inside a repo (not the workspace root), and a workspace-local skill wouldn't
  resolve there. Because they're symlinks, `git pull` in this repo upgrades them all.
- **The task database is per-workspace** — `<workspace>/.pwc/taskdb.db`, created on
  install (only if absent). Each workspace keeps its own tasks.

### Prerequisites

- **Python 3** (stdlib `sqlite3` — no packages needed for the task database).
- **iTerm2** with the Python API enabled (Preferences → General → Magic → *Enable
  Python API*) and `pip install iterm2` — required only for spawning workers.
- `pgrep` (standard on macOS) — for worker-status checks.

## Use

Start a Claude Code session **in the workspace** and:

- **`/pwc-setup-workspace`** — run once per workspace first: configures which external
  sources of work apply here (and re-run anytime to change them).
- **`/pwc-find-work`** — scans the configured sources for things that might be new
  tasks and queues the ones you confirm. The inbound edge.
- **`/pwc-show-work`** — the all-tasks briefing. Run it anytime (morning to orient,
  midday to check in, close to wrap up). Reconciles already-tracked tasks against
  their sources, sweeps for dead workers and stale tasks, recaps the day, and
  presents a prioritized view.
- **`/pwc-pick-work`** — suggests what to start or resume next. Suggests only; never acts
  without your confirmation.
- **`/pwc-start-work`** — turns a task into action: spawns a worker (default) or handles it
  inline. Also resumes a stopped task by reopening its prior session.
- **`/pwc-report-status`** — used *by a worker* to report status (blocked / awaiting-review
  / done / note) back to the task database.

## Inspecting the task database directly

All access normally goes through the coordinator, but the CLI is available for
debugging:

```bash
python3 scripts/taskdb.py --workspace <ws> summary
python3 scripts/taskdb.py --workspace <ws> detail --task t_0007
```

## Status

v1, built in phases and dogfooded against the `acme` workspace. Two behaviors are
deferred by design (the data/queries exist; the decision logic is learned from real
cases): **reconciliation conflict rules** and **new-task new-vs-update matching**.
See the build journal in [`docs/journal.md`](docs/journal.md).
