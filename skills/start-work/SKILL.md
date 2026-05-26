---
name: start-work
description: Act on a PWC task — either spawn a worker (a Claude Code session in its own iTerm2 tab) for substantial work, or handle it inline for trivial work. Also covers resuming a task whose worker has stopped. The default is to spawn a worker.
---

# /start-work

Turn a tracked task into action. `/start-work` decides whether the task warrants
its own **worker** (a Claude Code session in a new iTerm2 tab) or can be handled
**inline** by the coordinator, then does it. It also covers **resumption** — there
is no separate resume command; picking a stopped task back up is just starting
it again, reopening its prior session when one survives.

A worker is a normal Claude Code session that *you* drive — `/start-work` opens it
in the right repo with the task's context pre-loaded so you can begin immediately.
It does not coerce the session into acting autonomously; it sets it up and gets out
of the way.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace root**: the current directory (e.g. `~/work/acme`).
  A task's `workdir` is relative to this (a repo like `service-backend`, or the
  root itself). Requires **iTerm2 running with the Python API enabled** for spawning.

## Tools

- `python3 $SCRIPTS/taskdb.py detail --task <id>` — the task's fields, refs, and
  event timeline; the basis for the cwd, the resume decision, and the seed prompt.
- `python3 $SCRIPTS/worker_status.py --session-ids <uuid>` — whether the task's existing
  session (if any) is currently running.
- `python3 $SCRIPTS/spawn.py --task <id> --cwd <dir> --session-id <uuid> [--resume] [--prompt -]`
  — open the worker tab. Prints `{session_id, cwd, mode, transcript_expected}`.
- `python3 $SCRIPTS/taskdb.py set-session --task <id> --session-id <uuid> --workdir <dir>`
  — record the pre-allocated session id at spawn (atomic with a `dispatched` event).
- `python3 $SCRIPTS/taskdb.py update-task` / `log-event` — for inline outcomes.

## Steps

### Decide: inline vs. worker

1. **Default to a worker.** Spawn one for anything substantial — real coding,
   multi-step investigation, sustained back-and-forth. Reserve **inline** for the
   genuinely trivial that can't grow legs (a one-line Slack reply, a status check).
   The bias is deliberate: a needless spawn just wastes a tab (harmless), but
   inlining real work pollutes the coordinator's own context (the thing the whole
   design avoids). When unsure, spawn.

### Worker path

2. **Resolve the working directory.** Use the task's `workdir` (from `detail`)
   joined to the workspace root. If absent, infer from refs (e.g. a PR's repo) or
   ask the user. This exact cwd must be reused verbatim on any later resume — the
   session transcript is keyed by it.

3. **Decide fresh vs. resume.** If the task has a `session_id`, check it with
   `worker_status.py`:
   - **Alive** → the worker already exists. Don't spawn a duplicate; just point the
     user at its tab. Stop.
   - **Dead/gone, and its transcript still exists** → resume: call `spawn.py` with
     that same `--session-id` and `--resume`. The worker comes back with its full
     prior conversation.
   - **No session, or transcript gone** → fresh.

4. **For a fresh session, build a seed prompt that is *pure task context* — it
   requests no action at all.** The worker is a normal Claude Code session the user
   drives. Live testing showed that a fresh session will (correctly) refuse to *run
   any command* a seed message tells it to — even a "harmless" reporting command —
   because it can't verify an opaque script from an unrelated directory is safe just
   from a description. That refusal is good behavior, not a bug to defeat. So the
   seed must not instruct the worker to run *anything*. It only briefs:

   - **What the task is** — title, type, relevant refs (Jira key, PR, branch), and a
     short summary of the prior events/timeline so the worker starts oriented rather
     than cold.
   - **What "done" looks like** — the goal, so when the user starts driving the
     worker already understands the objective.

   That's it. No "run this command," no reporting instruction, no "stay open and
   wait." End with something like *"Ready when you are — what would you like to
   start with?"* so the session settles into a normal interactive state for the user
   to take over. For a resumed session, little or no seed is needed.

   **Don't put a reporting instruction in the seed.** PWC's skills are installed
   globally (`~/.claude/skills/`), so a worker *can* resolve `/report-status` — but
   a fresh worker still won't (and shouldn't) run a reporting command just because
   the opening message told it to. So leave reporting out of the seed entirely.
   Reporting happens later, once there's trust and context: the worker can run
   `/report-status` when *you* ask it to, or you can run `/report-status` from the
   coordinator to record where a task stands. Either way `/show-work`'s
   worker-status check still notices when a worker session ends and flags the task
   for triage, so nothing is lost if no explicit report is made.

5. **Pre-allocate and spawn.** Generate a UUID, pass it as `--session-id` to
   `spawn.py` (so the id is known before the process exists). Pipe the seed prompt
   via `--prompt -`.

6. **Record it immediately.** Right after spawn, run
   `taskdb.py set-session --task <id> --session-id <uuid> --workdir <dir>`. This
   writes the session id and a `dispatched` event so the task is tracked from the
   instant the worker starts — even one that dies on startup is recorded (the worker-status check
   will later mark it `gone`).

### Inline path

7. **Act directly** via the coordinator's own skills (e.g. `/slack-message`) and
   record the outcome with `taskdb.py log-event --task <id> --kind note --detail
   "..."` (and `update-task --status done` if it's finished). Do not spawn a tab.

8. **Promote if it grows.** If an inline task turns out bigger than expected, stop
   inlining and switch to the worker path (steps 2–6), seeding the new worker with
   what you've gathered so far. Don't keep absorbing growing work into the
   coordinator's context.

## Notes

- **Never resume a session that's still alive** — that's what the worker-status check in
  step 3 guards against.
- /start does not auto-pick tasks; it acts on a task the user chose (often via
  `/next`). It assumes the user has confirmed.
- spawn.py does not touch the task database; this skill owns the `set-session` write, so
  all coordinator-side DB writes go through one path.
