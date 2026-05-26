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

**If you are the coordinator session** (a session the user opened to manage work,
not a worker spawned to do one task), open by running `/show-work` so the user is
oriented. **If you are a worker** (you were given a specific task to work on), do
*not* run `/show-work` — just do your task.
<!-- PWC:END -->
