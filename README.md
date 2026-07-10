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
  bin/pwc                 # the CLI entry point (symlinked onto PATH by install.sh)
  scripts/                # shared deterministic mechanism ("the hands")
    taskdb.py             #   single read/write path to the task database (CLI, JSON I/O)
    spawn.py              #   spawn a worker in an iTerm2 window
    worker_status.py      #   is a worker session still running? (pgrep)
    sources.py            #   per-workspace sources config (read/write/validate)
    claude_md.py          #   splice the PWC section into a workspace CLAUDE.md
    pwc_db.py, _common.py #   shared connection / discovery helpers
  skills/                 # the coordinator's brain (SKILL.md each, pwc- prefixed)
    pwc-setup-workspace/  pwc-find-work/  pwc-show-work/  pwc-pick-work/
    pwc-start-work/  pwc-report-status/  pwc-show-task/
  install.sh              # symlink skills globally + init a workspace's task database
```

Runtime state lives **in each workspace**, never here: `<workspace>/.pwc/taskdb.db`.

## Install

```bash
./install.sh ~/work/acme     # or omit the arg for that default
```

Three parts:

- **Skills go global** — symlinked into `~/.claude/skills/` so *every* Claude Code
  session sees them, in any directory. This matters because a spawned worker runs
  inside a repo (not the workspace root), and a workspace-local skill wouldn't
  resolve there. Because they're symlinks, `git pull` in this repo upgrades them all.
- **The `pwc` CLI goes on PATH** — `bin/pwc` symlinked to `~/.local/bin/pwc`. One
  named command for the whole deterministic mechanism (`pwc summary`, `pwc sources
  show`, `pwc spawn …`, `pwc worker-status …`), runnable by the coordinator and by
  any worker in any directory — no `python3 <long path>` invocations in skills.
- **The task database is per-workspace** — `<workspace>/.pwc/taskdb.db`, created on
  install (only if absent). Each workspace keeps its own tasks.

### Prerequisites

- **Python 3** (stdlib `sqlite3` — no packages needed for the task database).
- **iTerm2** with the Python API enabled (Preferences → General → Magic → *Enable
  Python API*) and `pip install iterm2` — for spawning worker tabs.
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

Each task carries a **harness** (which coding agent runs its worker) and an
optional **model**. Both are set at queue time from the workspace's routing policy
(`"routing"` in `.pwc/sources.json`, read via `pwc sources routing`) and are
user-overridable; `/pwc-start-work` dispatches accordingly (`pwc spawn --harness …
--model …`). All three harnesses — **claude**, **opencode**, **codex** — are
session-tracked: the session id is known before the worker exists (claude:
caller-chosen uuid; opencode/codex: pre-created via their server APIs) and sits in
the worker's argv, so identity, `pgrep` liveness, and resume-by-id all work.

Tasks can also carry a **runhost** — a named always-on machine (registered in
`"runhosts"` in `.pwc/sources.json`) the worker runs on instead of this one.
A remote worker runs inside a **tmux session over SSH** (claude harness only for
now): the iTerm tab is just a viewport, so closing it — or the laptop sleeping —
doesn't stop the worker; reattach anytime with the spawn result's
`attach_command`. The seed is staged to a file on the remote host (no nested
quoting), liveness hops over ssh (`worker-status` rows take an `"ssh"` field;
an unreachable host reports `alive: null`, never "dead"), and resume is the same
pre-allocated session id on the same host.
- **`/pwc-report-status`** — used *by a worker* to report status (blocked / awaiting-review
  / done / note) back to the task database.

## Inspecting the task database directly

All access normally goes through the coordinator, but the CLI is available for
debugging:

```bash
pwc summary                      # workspace discovered from the cwd
pwc detail --task t_0007
pwc --workspace <ws> summary     # or explicit
```

## Status

v1, built in phases and dogfooded against the `acme` workspace. Two behaviors are
deferred by design (the data/queries exist; the decision logic is learned from real
cases): **reconciliation conflict rules** and **new-task new-vs-update matching**.
See the build journal in [`docs/journal.md`](docs/journal.md).
