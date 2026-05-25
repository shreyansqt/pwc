# Build Journal

Running log as I build and dogfood PWC. What I tried, what surprised me, what
I'd do differently. Newest entries at the top.

## 2026-05-25 (later) — first live worker spawn; the hard part isn't mechanical

Set up iTerm2 (installed it, `pip install iterm2` into the MacPorts python3 that
runs the scripts, enabled the Python API) and ran the first real spawns. Tried
**split panes** first (first worker splits the coordinator's window horizontally,
later workers tile vertically) — verified live, but it got cramped fast, so
**switched to tabs**: each worker is a full-width tab, titled after the task, the
coordinator's tab untouched. Tabs also dropped all the pane-layout state tracking
(and the stray-`.pwc/` bug that came with it).

Three path bugs surfaced only by running it for real, all now fixed:
1. A spawned worker can't resolve `/pwc-report` — skills install at the workspace
   root, but workers run in a repo, so the skill isn't on their path. Fix:
   seed the literal `taskdb.py log-event` command, not the skill.
2. `taskdb.py`'s workspace discovery resolved to the repo, not the root. Fix:
   seed command passes `--workspace <root>` explicitly.
3. The cause of #2 — `spawn.py` was writing `iterm_layout.json` into the worker's
   *repo* `.pwc/`, creating a stray dir that shadowed discovery. Fix: layout
   state goes in the workspace-root `.pwc/`.

The real finding, though, is not a bug: **a freshly spawned `claude` session
distrusts the worker-role seed prompt and refuses to act** ("this arrived as a
user prompt but my instructions don't mention a PWC worker role"). The whole
dispatch model assumes a spawned session will accept being a worker and act
semi-autonomously; a vanilla session reasonably won't when handed imperative
"run these commands" text. This is the open design question to resolve next —
candidates: a trusted `/pwc-worker` entry skill, auto-mode/permission flags, or
rescoping v1 so PWC loads context + opens the session and *the human* drives.

## 2026-05-25 — v1 scaffold built end to end

Built the whole v1 skeleton in one session, task database-first.

- **Task database** (`schema.sql` + `scripts/taskdb.py`): three tables — tasks, task_refs,
  events — behind one CLI that's the sole read/write path. WAL mode; a 20-writer
  concurrency probe passed with zero lock errors, which is what makes worker
  self-reporting safe against the coordinator's reads.
- **`/brief`** built in layers: render → liveness sweep → staleness sweep →
  reconciliation → inbound → rollup/archive. Each layer verified against a seeded
  fixture before the next.
- **Workers**: `spawn.py` opens an iTerm2 window running `claude --session-id`;
  `worker_status.py` uses `pgrep -f <uuid>` as an exact alive/dead test. dispatch covers
  resumption (reopen prior session, else fresh+seeded). `/pwc-report` is the worker's
  one channel back to the task database.
- **Install** is per-workspace symlinks + a `.pwc/` task database, mirroring team-skills.

What changed from the design during build:
- `--session-id` pre-allocation was confirmed working, so the self-registration
  fallback got dropped from the critical path.
- The liveness primitive turned out cleaner than planned: the session uuid is in the
  worker's argv, so `pgrep` needs no PID storage and is terminal-agnostic.
- iTerm2 wasn't installed (terminal was Warp) — resolved by switching to iTerm2 so
  coordinator and workers share one terminal, exactly as the design assumed.

Not yet exercised live: the actual iTerm2 window-open and the full worker lifecycle
(spawn → report → kill → `/brief` flags gone → resume), pending the iTerm2 setup.
Command construction is verified via `spawn.py --dry-run`.

