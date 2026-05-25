# Build Journal

Running log as I build and dogfood PWC. What I tried, what surprised me, what
I'd do differently. Newest entries at the top.

## 2026-05-25 — v1 scaffold built end to end

Built the whole v1 skeleton in one session, ledger-first.

- **Ledger** (`schema.sql` + `scripts/ledger.py`): three tables — tasks, task_refs,
  events — behind one CLI that's the sole read/write path. WAL mode; a 20-writer
  concurrency probe passed with zero lock errors, which is what makes worker
  self-reporting safe against the coordinator's reads.
- **`/brief`** built in layers: render → liveness sweep → staleness sweep →
  reconciliation → inbound → rollup/archive. Each layer verified against a seeded
  fixture before the next.
- **Workers**: `spawn.py` opens an iTerm2 window running `claude --session-id`;
  `liveness.py` uses `pgrep -f <uuid>` as an exact alive/dead test. dispatch covers
  resumption (reopen prior session, else fresh+seeded). `/pwc-report` is the worker's
  one channel back to the ledger.
- **Install** is per-workspace symlinks + a `.pwc/` ledger, mirroring team-skills.

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

