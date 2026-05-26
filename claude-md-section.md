<!-- PWC:START (managed by install.sh — edit in the pwc repo, not here) -->
## PWC — work coordination

This workspace uses **PWC** to track in-flight work. Tasks live in a local database
(`.pwc/taskdb.db`); the commands are global skills:

- `/setup-workspace` — configure which sources of work this workspace draws from (once).
- `/find-work` — scan those sources for new tasks and queue the ones you confirm.
- `/show-work` — see where all tracked work stands (the briefing).
- `/pick-work` — suggest what to do next.
- `/start-work` — act on a task: spawn a worker (own tab) or handle it inline.
- `/report-status` — record where a task stands.

To see where your work stands, run `/show-work`. If you were spawned as a worker
on a specific task, just do that task — the PWC commands are for coordinating work,
not for a worker to run.
<!-- PWC:END -->
