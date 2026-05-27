---
name: pwc-start-work
description: Act on a PWC task — either spawn a worker (a Claude Code session in its own iTerm2 tab) for substantial work, or handle it inline for trivial work. Also covers resuming a task whose worker has stopped. The default is to spawn a worker.
---

# /pwc-start-work

Turn a tracked task into action. `/pwc-start-work` decides whether the task warrants
its own **worker** (a Claude Code session in a new iTerm2 tab) or can be handled
**inline** by the coordinator, then does it. It also covers **resumption** — there
is no separate resume command; picking a stopped task back up is just starting
it again, reopening its prior session when one survives.

A worker is a normal Claude Code session that *you* drive — `/pwc-start-work` opens it
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
  — open the worker tab and type the seed into its input box (without submitting).
  Prints `{session_id, cwd, mode, transcript_expected, seed}` where `seed` is
  `in-box` / `skipped` / `not-typed`.
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

   - **Its PWC task id, stated first as the handle.** Open with e.g. *"Your PWC task
     id is `SMT-921`."* This is the durable key back to the task's row: the worker (or
     the user driving it) can re-fetch the full, current context — fields, refs,
     event history — anytime with `python3 $SCRIPTS/taskdb.py detail --task SMT-921`
     (spell out the actual `$SCRIPTS` path and id). Mention this once as *available*
     if context is lost or the latest refs/notes are wanted — don't tell the worker
     to run it now (a fresh worker won't run an opaque script on command, by design;
     see below). The point is that the id and the how-to are recorded in the seed, so
     the path exists when needed.
   - **What the task is** — title, type, relevant refs (Jira key, PR, branch), and a
     short summary of the prior events/timeline so the worker starts oriented rather
     than cold.
   - **What "done" looks like** — the goal, so when the user starts driving the
     worker already understands the objective.
   - **A closing-report instruction** — ask the worker, *once it has finished the
     work or hit a blocker it can't clear*, to record where it landed by running
     `/pwc-report-status` for this task id (the right `--kind` — `done`/`blocked`/
     `awaiting-review` — with `--set-status` to match, and `--workspace <root>` since
     a worker runs inside a repo). This is a *closing* step, explicitly scoped to
     "when you're done," so the coordinator's board reflects the outcome without the
     user hand-reconciling. See the timing rule below.

   That's it on context. End with something like *"Ready when you are — what would
   you like to start with?"* so the session settles into a normal interactive state
   for the user to take over. For a resumed session, little or no seed is needed.

   **Reporting: at completion, not on startup.** PWC's skills are installed globally
   (`~/.claude/skills/`), so a worker can resolve `/pwc-report-status` — and because
   it's a real, named skill (not an opaque shell line), a warmed-up worker can invoke
   it deliberately. The one hard rule: **don't ask the worker to report *on spawn* or
   mid-flight** — a fresh worker shouldn't run anything before it's done real work,
   and `/pwc-show-work` already notices a vanished session on its own. So the seed's
   reporting ask is strictly the *closing* step ("when you're done or blocked, run
   `/pwc-report-status`"), never "report now" and never "report at every step." The
   user can also run `/pwc-report-status` from the coordinator at any time; and if the
   worker ends without reporting, `/pwc-show-work`'s worker-status check still flags
   it (and preserves any status it did report), so nothing is lost.

5. **Pre-allocate and spawn.** Generate a UUID, pass it as `--session-id` to
   `spawn.py` (so the id is known before the process exists). Pipe the seed prompt
   via `--prompt -`.

   **The seed is placed in the worker's input box, NOT auto-submitted.** `spawn.py`
   types the briefing into the new session's prompt box and stops — it does not press
   Enter. This is deliberate: auto-submitting raced claude's startup and the
   keystrokes were silently lost, and it gave the user no chance to read the briefing
   first. The spawn result reports the outcome in `seed`: `"in-box"` (typed and
   waiting), `"skipped"` (no seed), or `"not-typed"` (the TUI never drew within the
   timeout, so the seed was NOT typed — tell the user to paste it manually).

6. **Record it, then tell the user to press Enter.** Right after spawn, run
   `taskdb.py set-session --task <id> --session-id <uuid> --workdir <dir>` (writes the
   session id and a `dispatched` event, so the task is tracked from the instant the
   worker starts — even one that dies on startup is recorded; the worker-status check
   will later mark it `gone`). Then, in your reply to the user, **explicitly tell them
   the seed briefing is sitting in the new tab's input box and they just need to
   review it and press Enter to start the worker.** Do not claim the worker is already
   running — it isn't until the user submits. If `seed` came back `"not-typed"`, tell
   them the briefing was not entered and to paste it themselves (the exact text is
   what you piped in).

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
