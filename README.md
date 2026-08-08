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

Runtime state lives **in each workspace**, never here: `<workspace>/.pwc/taskdb.db`
— or, for a **hub-backed** workspace, in your own deployed hub (see below).

## The hub (optional): your task databases in your own Cloudflare account

By default every workspace is a local SQLite file. `hub/` is a public template —
a small Worker + D1 database — that any workspace can graduate onto, so the task
database survives any single machine, is restorable point-in-time (D1 Time
Travel), and is writable by workers on remote machines over HTTPS (no VPN):

- Deploy your own instance (`hub/README.md`; keep the real ids in a small
  private repo). One deployment serves many workspaces.
- Flip a workspace: `pwc export > dump.json`, POST it to `/w/<name>/import`,
  write `<workspace>/.pwc/store.json` (`{"store": "hub", "url": …, "workspace":
  …, "token_file": …}`), and rename the old `taskdb.db` away. `pwc export` works
  against the hub too — leaving is one command, there is no lock-in.
- Skills and the CLI are backend-blind: `pwc summary` is `pwc summary` either
  way (`hub/conformance.py` enforces that — 26/26 ops structurally identical).
- `sources.json`, `store.json`, and worker sessions/transcripts stay per-machine;
  only the task database moves.

Design + rejected alternatives: [`docs/hub-design.md`](docs/hub-design.md).

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
optional **model** — chosen automatically, and cost-aware.

## Routing: the cheapest model that can actually do the job

`/pwc-find-work` profiles each task as it queues it — *what kind of work is this
(code-review / implementation / research-writing / ops-comms), how much reasoning does
it really need (1-5), how cheaply would a wrong answer be caught (1-5), does it touch
production data?* — and hands that profile to **`pwc route`**, which picks the
**cheapest model whose capability clears the bar**. The task states what it needs; the
table decides who serves it. Adding a new model is a new row, not a code change.

```
$ pwc route --domain implementation --reasoning 3 --verifiability 4
→ opencode / openrouter/deepseek/deepseek-v4-pro   ($0.13/Mtok)
$ pwc route --domain code-review --reasoning 4 --verifiability 2
→ claude / opus  (raised to tier 5: low verifiability — a wrong answer wouldn't be caught)
```

The models live in a **global model table** (`~/.config/pwc/model-table.json`): cost,
context window, and a 1-5 capability tier per domain. `pwc models fetch` refreshes the
objective columns from OpenRouter's free catalog; `/pwc-find-work` checks staleness
(>7 days) and **proposes** the diff for confirmation rather than applying it silently.
Your own tier corrections live in a separate **overlay** that a refresh cannot touch —
so the table converges on *your* experience, not on vendor benchmarks.

Two deliberate hard edges:

- **No fallback chains.** If nothing qualifies, `route` refuses and says what filtered
  everything out. Silently downgrading to a model already judged unfit is how a "cheap"
  run quietly produces garbage on a task that needed care.
- **`prod-data` filters on trust, not just capability.** "Good enough" and "allowed to
  see customer data" are different questions; a model must be explicitly `trusted` to
  receive real data, no matter how capable or cheap it is.

## What it actually cost

**`pwc cost`** measures real spend from each harness's own session store — claude
transcripts, codex rollouts, opencode's sqlite:

```
$ pwc cost --task breezemail        → $1.01   (claude/opus)
$ pwc cost --report                 → per-harness rollup
```

It stores **tokens, not dollars** — prices move, so a stored dollar figure freezes
history and makes "what would this have cost on DeepSeek?" unanswerable. Dollars are
derived at report time against whatever price set you ask about. Cost is also **re-read
live rather than frozen at close**, because a worker keeps spending after its task is
done (the follow-up tweak, the docs pass).

Note that **subscription tokens are still priced at rack rate**. Claude and Codex ride
subscriptions, so their figure is *fair-value* — what those tokens would have cost on
the open market, not money billed. That is exactly the number that tells you whether a
cheaper plan would cover your usage; pricing them at zero would make the router
maximize the spend you're trying to evaluate.

At task close, `/pwc-report-status` measures the cost and asks one question — *was that
model right?* (too weak / about right / overkill) — and writes the answer into the
overlay, so routing gets better rather than staying a guess.

All three harnesses — **claude**, **opencode**, **codex** — are
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

## Starting a coordinator

```bash
pwc coord                  # claude (default), model derived from the models table
pwc coord codex            # same, on a different harness
pwc coord claude --model sonnet
```

Run it from wherever you want the coordinator to sit — usually a workspace root, or
a PARENT of several workspaces (`pwc` sweeps them all and tags each row).

`pwc coord` **replaces the current shell** (`os.execvp`): the tab you typed in
becomes the coordinator. That is deliberate, and it is also what makes the tab title
work. An OSC title sequence only reaches the terminal from a process that owns the
tty, and a coordinator cannot title its own tab — harness tool subprocesses run
detached, so a `printf` from inside a session goes into a pipe and is silently
swallowed. `pwc coord` sets the title from the interactive shell, before exec, and
passes the same name to the harness.

The model is derived from the models table (strongest available for that harness,
scored across the coordinator's `research-writing`+`ops-comms` domains, ties broken
toward the cheaper model), so a coordinator cannot start on a weak model by
accident. `pwc models set-tier` retunes that without touching code. It seeds
`/pwc-show-work`, so the briefing is already running when the session opens.

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
