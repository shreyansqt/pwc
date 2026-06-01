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
  Read it for *routing* — workdir, whether there's a session to resume, and the refs
  to name in the seed. Do **not** use it as license to go read the linked thread/PR/
  Jira yourself; pulling that underlying content into the coordinator's context is the
  worker's job, not yours. The seed names the refs and the task id; the worker derives
  the substance.
- `python3 $SCRIPTS/worker_status.py --session-ids <uuid>` — whether the task's existing
  session (if any) is currently running.
- `python3 $SCRIPTS/spawn.py --task <id> --cwd <dir> --session-id <uuid> [--resume] [--prompt -] [--name "<id> · <gist>"]`
  — open the worker tab and type the seed into its input box (without submitting).
  Prints `{session_id, cwd, mode, transcript_expected, seed}` where `seed` is
  `in-box` / `skipped` / `not-typed`.
- `python3 $SCRIPTS/taskdb.py set-session --task <id> --session-id <uuid> --workdir <dir>`
  — record the pre-allocated session id at spawn (atomic with a `dispatched` event).
- `python3 $SCRIPTS/taskdb.py clear-session --task <id>` — the inverse: NULL the
  session_id (logs a neutral note, not a dispatch; status untouched). Use to back out
  a session recorded by mistake, or to detach a finished/abandoned one so the task
  reads as not-dispatched.
- `python3 $SCRIPTS/sources.py skill-hints [--type <type>]` — the configured
  task-type → skill(s) map. Optional now that the minimal seed leans on
  `/pwc-show-task`; consult only if you want to name an obvious tool in the seed.
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
     prior conversation. **On a resume, `spawn.py` intentionally does not type a seed**
     (the worker already carries its context), so the result returns `"seed":
     "skipped"` — this is correct, not a failure. Any `--prompt` you pipe on a resume
     is dropped. So tell the user the worker resumed with its history (and to just
     continue in the tab) — **don't** promise an in-box briefing to review the way you
     would for a fresh spawn. If you want to nudge the resumed worker in a specific
     direction, say so to the user as text to paste, since the seed won't be typed.
   - **No session, or transcript gone** → fresh.

4. **For a fresh session, build a *minimal* seed: the task id, a skill to load its
   own context, and the closing-report step. Don't inline the task's content.** The
   seed is delivered as un-submitted text in the worker's input box (see step 5) —
   *the user* reads it and presses Enter, so the worker's first action is whatever the
   seed says, submitted by the user, not a command the worker runs unprompted. That
   distinction matters: a fresh worker rightly won't *auto-run an opaque shell script*
   it was merely told about (e.g. a raw `python3 $SCRIPTS/taskdb.py …` line — it can't
   verify an unfamiliar script is safe). But a **named, installed skill** like
   `/pwc-show-task`, submitted by the user as the first instruction, is a normal,
   trusted invocation. So the seed leans on the skill to self-load context rather than
   carrying that context inline. The seed has just three parts:

   - **The PWC task id, stated first as the handle** — e.g. *"Your PWC task is
     `SMT-921`."* This is the durable key to everything else.
   - **An instruction to load its own context via `/pwc-show-task <id>`** — e.g. *"Run
     `/pwc-show-task SMT-921` to pull your full context (fields, refs, event timeline)
     from the task DB, then start on it."* Pass the id with the skill so resolution is
     trivial (the skill's session-inference fallback is for when the id is lost — don't
     rely on it). The worker pulls the *current* fields/refs/timeline itself — no stale
     snapshot baked into the seed, and nothing for the coordinator to transcribe. This
     replaces the old inlined title/refs/event-dump entirely.
   - **The closing-report step** — *"When you've finished or hit a blocker you can't
     clear, run `/pwc-report-status` for this task."* (`/pwc-show-task` and
     `/pwc-report-status` together also surface the relevant skills for the work, so a
     separate `skill-hints` lookup for the seed is no longer needed; if you want to
     name an obvious tool, one line is fine, e.g. *"output is a Slack message →
     `/slack-message`"*.)

   Keep it to a few lines. End with something like *"Ready when you are."* so the
   session settles into a normal interactive state. For a resumed session, no seed is
   typed at all (step 3).

   **Reporting: at completion, not on startup.** The `/pwc-report-status` ask is
   strictly the *closing* step ("when you're done or blocked") — never "report now" or
   "report at every step." `/pwc-show-work`'s worker-status check already notices a
   vanished session and preserves any status the worker did report, so nothing is lost
   if the worker ends without reporting. The user can also run `/pwc-report-status`
   from the coordinator at any time.

5. **Pre-allocate and spawn.** Generate a UUID, pass it as `--session-id` to
   `spawn.py` (so the id is known before the process exists). Pipe the seed prompt
   via `--prompt -`.

   **Give the tab a scannable title via `--name`.** Without it the tab is just the
   bare task id, which is hard to tell apart across many tabs. Pass
   `--name "<id> · <short gist>"` — the id plus a 3–5 word gist distilled from the
   task title (e.g. `SMT-677 · BO auth review`, `SMT-921 · SevDesk sync fix`,
   `slack-ocr · OCR income prefill`). Keep it short (a tab is narrow); shorten a long
   id to a recognizable stub if needed. The full id still lives in the seed and DB.

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

   **Before closing or re-pointing a task inline, verify against the source — then
   write the context, then change the status.** If the inline action is destructive or
   hard to undo (closing as `done`, re-pointing at a different ticket, merging,
   dropping), don't do it on a worker's reported note or on the user's recollection
   alone ("Stella already replied / created a ticket"). **Re-read the actual cited
   source** (the Slack thread, the PR, the Jira ticket) and confirm the outcome first —
   the user pointing you at it is the trigger to verify, not a substitute for
   verifying. Then **log the verified context before the status flip**: a `note` with
   what the source actually said and the decision, plus any `title` / `workdir` / ref
   fixes so the task matches reality — *then* `update-task --status`. A task closed or
   re-scoped with only a one-line "done" loses the context the next person (or future
   you) needs; the justifying note must land with the change, not after it.

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
