---
name: pwc-start-work
description: Act on a PWC task — either dispatch a worker (a Claude Code session, spawned in its own iTerm2 tab or, in Claude Desktop mode, handed to the user to open) for substantial work, or handle it inline for trivial work. Also covers resuming a task whose worker has stopped. The default is to dispatch a worker.
---

# /pwc-start-work

Turn a tracked task into action. `/pwc-start-work` decides whether the task warrants
its own **worker** (a Claude Code session) or can be handled **inline** by the
coordinator, then does it. How a worker is launched depends on the workspace's
**mode**: in **`iterm2`** mode it's spawned in a new tab; in **`desktop`** mode (a
Claude Desktop user with no terminal) the seed is handed to the user to open the
session themselves. It also covers **resumption** — there is no separate resume
command; picking a stopped task back up is just starting it again, reopening its
prior session when one survives (iterm2 mode only — a desktop handoff has no live
process to reopen).

A worker is a normal Claude Code session that *you* drive — `/pwc-start-work` opens it
in the right repo with the task's context pre-loaded so you can begin immediately.
It does not coerce the session into acting autonomously; it sets it up and gets out
of the way.

## Configuration

- **Scripts directory**: `~/work/side-projects/pwc/scripts` (`$SCRIPTS`).
- **Workspace root**: the current directory (e.g. `~/work/acme`).
  A task's `workdir` is relative to this (a repo like `service-backend`, or the
  root itself).
- **Launch mode**: read `python3 $SCRIPTS/sources.py mode` first — it returns
  `{"mode": "iterm2"|"desktop"}` (default `iterm2`). **`iterm2`** spawns a worker tab
  (requires iTerm2 with the Python API enabled). **`desktop`** is for a Claude
  Desktop user with no terminal: instead of spawning, you **hand the user the seed**
  to open a session themselves. The worker path below branches on this at step 5.

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
- `python3 $SCRIPTS/sources.py mode` — the launch mode (`iterm2` | `desktop`). Read
  it first; it decides which dispatch tool below you use.
- **(iterm2 mode)** `python3 $SCRIPTS/spawn.py --task <id> --cwd <dir> --session-id <uuid> [--resume] [--prompt -] [--name "<id> · <gist>"]`
  — open the worker tab and type the seed into its input box (without submitting).
  Prints `{session_id, cwd, mode, transcript_expected, seed}` where `seed` is
  `in-box` / `skipped` / `not-typed`.
- **(desktop mode)** `python3 $SCRIPTS/handoff.py --task <id> --cwd <dir> --session-id <uuid> [--prompt -] [--name "<id> · <gist>"]`
  — copies the seed to the clipboard (`pbcopy`) and prints
  `{session_id, cwd, seed, clipboard}` where `clipboard` is `copied` / `failed` /
  `skipped`. Does NOT spawn anything — the user opens the session themselves. There is
  no resume concept here (no live process to reopen); a "resume" is just a fresh
  handoff with the same `--session-id`.
- `python3 $SCRIPTS/taskdb.py set-session --task <id> --session-id <uuid> --workdir <dir>`
  — record the pre-allocated session id at spawn (atomic with a `dispatched` event).
- `python3 $SCRIPTS/taskdb.py clear-session --task <id>` — the inverse: NULL the
  session_id (logs a neutral note, not a dispatch; status untouched). Use to back out
  a session recorded by mistake, or to detach a finished/abandoned one so the task
  reads as not-dispatched.
- `python3 $SCRIPTS/sources.py skill-hints [--type <type>]` — the configured
  task-type → skill(s) map. **Run it with `--type <task-type>` while building every
  fresh seed** (step 4): if it returns skills for the type, the seed must strongly
  recommend them. This is the workspace's deliberate routing — e.g. `pr-review` →
  `code-review` — so a PR-review worker always starts from `/code-review`, not from
  whatever tool the coordinator happens to think of. The map is the source of truth
  for "which skill runs this kind of work," not the coordinator's judgement.
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

3. **Decide fresh vs. resume.** **(desktop mode: skip the liveness check — a Desktop
   worker is not a local process, so `worker_status.py`/`pgrep` can't see it. Treat
   it as fresh and re-hand-off the seed with the task's existing `session_id` if it
   has one. There's no live tab to point the user back to.)** In **iterm2 mode**, if
   the task has a `session_id`, check it with `worker_status.py`:
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
   - **No live `session_id` on the task → do NOT jump to fresh yet. Look back through
     the event log for a prior session to resume first.** A task's `session_id` is
     *detached* (cleared) every time a worker reports `done`/`blocked`/`note` and its
     tab closes — so a task that was worked, then **blocked, then later unblocked**
     reads as "no session" even though a fully-resumable session with all the prior
     investigation is sitting in its history. This is the common shape for an unblocked
     task (e.g. "tested the endpoint, filed a support ticket, blocked on their reply" →
     reply arrives → resume the worker that did the testing, don't start one that has
     to rediscover it). So before spawning fresh: run `taskdb.py detail --task <id>`,
     scan the `events` for the most recent `dispatched` event's `session_id`, and check
     whether its transcript still exists (`worker_status.py` for liveness; the
     transcript path is `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`). If a prior
     session's transcript survives, **resume that** (`spawn.py --session-id <that-id>
     --resume`) and nudge it toward the new development (the unblock) as paste text —
     its context is worth far more than a clean slate. Only when there is genuinely no
     prior session, or every prior transcript is gone, do you spawn **fresh**. Spawning
     fresh on an unblocked task whose original session is still resumable is a real
     defect — it throws away the exact context the task needs.

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
   - **The recommended-skill step** — run `skill-hints --type <task-type>` and, if it
     returns any skills, **strongly recommend them in the seed as the way to do the
     work**, not as an optional aside. Phrase it as a directive: *"Review this with
     `/code-review`"* / *"Draft the reply with `/slack-message`"* — not *"you could
     use…"*. This is the workspace's configured routing (e.g. `pr-review` →
     `code-review`), so it should land the same way every time regardless of which
     coordinator instance builds the seed. The worker can still deviate if the skill
     genuinely doesn't fit, but the default the seed points at is the configured one.
   - **The closing-report step** — *"When you've finished or hit a blocker you can't
     clear, run `/pwc-report-status` for this task."*
   - **The attach-threads step** — *"If you post to or read any Slack thread about
     this task, attach it to the task as a working ref (via `/pwc-report-status` /
     `add-ref`, using the message's real `thread_ts`) so replies get noticed later."*
     This is what keeps a teammate's later answer from being silently missed: the
     find-work sweep can only check threads that are attached to the task. Cheap to
     say in the seed, and it closes the loop on the most common blind spot.

   Keep it to a few lines. End with something like *"Ready when you are."* so the
   session settles into a normal interactive state. For a resumed session, no seed is
   typed at all (step 3).

   **Reporting: at completion, not on startup.** The `/pwc-report-status` ask is
   strictly the *closing* step ("when you're done or blocked") — never "report now" or
   "report at every step." `/pwc-show-work`'s worker-status check already notices a
   vanished session and preserves any status the worker did report, so nothing is lost
   if the worker ends without reporting. The user can also run `/pwc-report-status`
   from the coordinator at any time.

5. **Pre-allocate and dispatch.** Generate a UUID and pass it as `--session-id` (so
   the id is known before any process exists). Pipe the seed via `--prompt -`. Pass a
   scannable `--name "<id> · <short gist>"` — the id plus a 3–5 word gist from the
   title (e.g. `SMT-677 · BO auth review`, `slack-ocr · OCR income prefill`). The tool
   you call depends on the mode read in the Configuration:

   **iterm2 mode → `spawn.py`.** Opens the worker tab and types the seed into its
   input box, **NOT auto-submitted** — it stops before Enter. (Auto-submitting raced
   claude's startup and the keystrokes were lost, and it gave the user no chance to
   read the briefing first.) The `--name` titles the tab. The result reports `seed`:
   `"in-box"` (typed and waiting), `"skipped"` (no seed), or `"not-typed"` (the TUI
   never drew within the timeout, so the seed was NOT typed — tell the user to paste
   it manually).

   **desktop mode → `handoff.py`.** There is no terminal to spawn into, so this
   **does not open anything** — it copies the seed to the clipboard (`pbcopy`) and
   returns `clipboard`: `"copied"` / `"failed"` / `"skipped"`. The user opens a new
   session themselves. Nothing is auto-submitted because there's nothing to submit
   into yet.

6. **Record it, then tell the user how to start the worker.** Right after dispatch,
   run `taskdb.py set-session --task <id> --session-id <uuid> --workdir <dir>` (writes
   the session id and a `dispatched` event, so the task is tracked from the instant
   the worker starts), and **move the task to `in-progress`**
   (`taskdb.py update-task --task <id> --status in-progress`) — dispatching a worker is
   what flips a task from `pending` to `in-progress`. (On a resume of an already-started
   task it's already in-progress; setting it again is harmless.) Then, in your reply:

   - **iterm2 mode:** **explicitly tell the user the seed briefing is sitting in the
     new tab's input box and they just need to review it and press Enter.** Do not
     claim the worker is already running — it isn't until they submit. If `seed` came
     back `"not-typed"`, tell them it was not entered and to paste it themselves.

   - **desktop mode:** tell the user to **open a new Claude Code session in the Desktop
     app's code section, in directory `<the resolved cwd>`, and paste** (the seed is on
     their clipboard if `clipboard` is `"copied"`). Give them the **exact directory**
     and, if `clipboard` is `"failed"`/`"skipped"`, **include the full seed text** in
     your reply for them to copy by hand. Don't claim the worker is running — it isn't
     until they open the session and submit. (No tab, no `pgrep`-able process, so
     `/pwc-show-work` won't auto-detect it; the user reports status via
     `/pwc-report-status` when there's something to record.)

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
  step 3 guards against (iterm2 mode; in desktop mode there's no live process to
  check, so always re-hand-off rather than resume).
- /start does not auto-pick tasks; it acts on a task the user chose (often via
  `/next`). It assumes the user has confirmed.
- spawn.py does not touch the task database; this skill owns the `set-session` write, so
  all coordinator-side DB writes go through one path.
