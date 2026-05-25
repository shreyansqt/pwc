---
name: dispatch
description: Act on a PWC task — either spawn a worker (a Claude Code session in its own iTerm2 tab) for substantial work, or handle it inline for trivial work. Also covers resuming a task whose worker has stopped. The default is to spawn a worker.
---

# dispatch

Turn a tracked task into action. dispatch decides whether the task warrants its own
**worker** (a Claude Code session in a new iTerm2 tab) or can be handled
**inline** by the coordinator, then does it. It also covers **resumption** — there
is no separate resume command; picking a stopped task back up is just dispatching
it again, reopening its prior session when one survives.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace root**: the current directory (e.g. `~/work/acme`).
  A task's `workdir` is relative to this (a sub-repo like `service-backend`, or the
  root itself). Requires **iTerm2 running with the Python API enabled** for spawning.

## Tools

- `python3 $SCRIPTS/taskdb.py detail --task <id>` — the task's fields, refs, and
  event timeline; the basis for the cwd, the resume decision, and the seed prompt.
- `python3 $SCRIPTS/liveness.py --session-ids <uuid>` — whether the task's existing
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
   `liveness.py`:
   - **Alive** → the worker already exists. Don't spawn a duplicate; just point the
     user at its tab. Stop.
   - **Dead/gone, and its transcript still exists** → resume: call `spawn.py` with
     that same `--session-id` and `--resume`. The worker comes back with its full
     prior conversation.
   - **No session, or transcript gone** → fresh.

4. **For a fresh session, build a seed prompt** from the task's detail + event
   timeline so the worker continues rather than starting cold. Include: the task id,
   what it is, relevant refs (Jira key, PR, branch), a short summary of prior events,
   and **the exact status-reporting command** (see below). For a resumed session,
   little or no seed is needed.

   **Reporting instruction — give the worker the full script command, not the
   skill.** A worker runs in a sub-repo (e.g. `service-banking`), which creates two
   traps to avoid:
   - `/pwc-report` is *not* resolvable from the worker's cwd (skills install at the
     workspace root), so seed the literal `taskdb.py` command instead.
   - The task database lives at the *workspace root* (`<workspace>/.pwc/taskdb.db`), but
     `taskdb.py` auto-discovers from cwd — which, from a sub-repo, finds the wrong
     place. So the command **must pass `--workspace <root>` explicitly.**

   Seed the worker with exactly this (substitute the real workspace root and task id):
   ```
   To report status, run this exact command (works from any directory):
     python3 ~/work/pwc/scripts/taskdb.py \
       --workspace ~/work/acme \
       log-event --task <THIS-TASK-ID> --source worker \
       --kind <blocked|awaiting-review|done|note> --detail "<what happened>"
   Report when you hit a meaningful event (blocked, up for review, done).
   ```
   Do not tell the worker to use `/pwc-report`, and do not omit `--workspace`.

5. **Pre-allocate and spawn.** Generate a UUID, pass it as `--session-id` to
   `spawn.py` (so the id is known before the process exists). Pipe the seed prompt
   via `--prompt -`.

6. **Record it immediately.** Right after spawn, run
   `taskdb.py set-session --task <id> --session-id <uuid> --workdir <dir>`. This
   writes the session id and a `dispatched` event so the task is tracked from the
   instant the worker starts — even one that dies on startup is recorded (liveness
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

- **Never resume a session that's still alive** — that's what the liveness check in
  step 3 guards against.
- dispatch does not auto-pick tasks; it acts on a task the user chose (often via
  `/next`). It assumes the user has confirmed.
- spawn.py does not touch the task database; this skill owns the `set-session` write, so
  all coordinator-side DB writes go through one path.
