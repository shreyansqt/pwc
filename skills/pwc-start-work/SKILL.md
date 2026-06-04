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
  — open the worker tab and launch claude with the seed as its positional prompt, so
  claude **auto-submits the seed on startup** (the worker starts working immediately;
  there is no review-then-Enter step). Prints
  `{session_id, cwd, mode, transcript_expected, seed}` where `seed` is `submitted`
  (baked into the launch command and auto-submitted) or `skipped` (no seed — e.g. a
  resume). The seed is no longer typed into the input box, so there is no `not-typed`
  failure mode and nothing for the user to paste by hand.
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
     continue in the tab) — **don't** describe it as auto-starting on a fresh seed the
     way a fresh spawn does. If you want to nudge the resumed worker in a specific
     direction, say so to the user as text to paste, since no seed is submitted.
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

4. **For a fresh session, build a *minimal* seed that hands the worker the task and
   tells it to investigate and come back with a plan — NOT how to do the work.** This
   is the core rule: the coordinator's seed says *what* the task is and points at how
   to load context; deciding *how* to do the work is for the worker and the user to
   settle together, not for the coordinator to bake in. Do **not** write seeds like
   "read the two epics, pick a shortlist, then move them to Ready-for-Dev" or "start
   the ticket with `/start-ticket` and scope it to smarta-accounts" — that front-runs
   the user's call and sends the worker executing on the coordinator's plan. Instead
   the worker should: load its context, investigate enough to understand the task,
   then **stop and present its understanding plus proposed approaches/options and its
   own recommendation, and ask the user how to proceed** — and only start executing
   once the user has chosen a direction.

   The seed is passed as claude's positional prompt and **auto-submitted on startup**
   (see step 5) — so the worker's first action *is* the seed, run as soon as it boots.
   That auto-start is exactly why the seed must steer toward *investigate-then-ask*,
   not *do*: an auto-submitted "do X" prompt means the worker starts changing things
   before the user has weighed in. An auto-submitted "understand X, then propose
   options and ask me" prompt is safe — it spends the head start on reading, not on
   acting. It also makes it doubly important that the seed points at **named, installed
   skills** the worker can trust and run (like `/pwc-show-task`), not opaque shell like
   a raw `python3 $SCRIPTS/taskdb.py …` line it can't verify and shouldn't auto-run.

   **The hard gate: investigate freely, change nothing until the user picks an
   approach.** The worker may read/search/inspect anything — repo, Jira, PR diff,
   Slack threads, logs — to understand the work. But it must **not take any
   state-changing or outward action** until the user has agreed an approach: no writing
   or editing code, no creating branches or PRs, no moving/editing Jira tickets, no
   posting to Slack, no running mutating jobs. Reaching the point where it *could* act
   is the cue to stop and ask, not to proceed. (This gate applies to **all task
   types** — jira, pr-review, slack, project alike.)

   Build the seed from these parts:

   - **The PWC task id, stated first as the handle** — e.g. *"Your PWC task is
     `SMT-921`."* This is the durable key to everything else.
   - **An instruction to load its own context via `/pwc-show-task <id>`** — e.g. *"Run
     `/pwc-show-task SMT-921` to pull your full context (fields, refs, event timeline)
     from the task DB."* Pass the id with the skill so resolution is trivial (the
     skill's session-inference fallback is for when the id is lost — don't rely on it).
     The worker pulls the *current* fields/refs/timeline itself — no stale snapshot
     baked into the seed, and nothing for the coordinator to transcribe.
   - **A one-line statement of the task's goal/intent** — *what* outcome the task is
     after, drawn from the title/notes, so the worker knows the target. This is the
     *only* substantive content the seed carries, and it describes the destination, not
     the route. Do not spell out steps, tools, or sequencing to get there.
   - **The investigate-then-propose-then-ask directive** — phrase it as: *"Investigate
     enough to understand this task, then STOP and come back to me with your
     understanding, the approaches you see (with trade-offs) and your recommendation,
     and ask how I'd like to proceed. Don't make any change — no code, no branch, no
     ticket move, no Slack post, no job — until we've agreed an approach."* This is the
     part that puts the *how* decision back with the worker + user.
   - **The skill pointer, framed as the tool to use *once an approach is agreed*** — run
     `skill-hints --type <task-type>` and, if it returns any skills, mention them as the
     likely tool for *carrying out* the work after the user has chosen a direction — NOT
     as a "do this now" directive. Phrase it as *"Once we've agreed how to proceed,
     `/code-review` is likely the right way to carry it out"* / *"…`/start-ticket` is
     how you'll set up the implementation"* — never *"Review this with /code-review"* as
     the worker's opening move. The workspace's configured routing (e.g. `pr-review` →
     `code-review`) still names the right tool; the change is purely *when* — after the
     plan is agreed, not before. (A pure-research/review task whose whole point is
     "investigate and report" naturally satisfies the gate, since reporting IS the
     deliverable — there the skill and the propose-back step coincide.)
   - **The closing-report step** — *"When you've finished or hit a blocker you can't
     clear, run `/pwc-report-status` for this task."*
   - **The attach-threads step** — *"If you post to or read any Slack thread about
     this task, attach it to the task as a working ref (via `/pwc-report-status` /
     `add-ref`, using the message's real `thread_ts`) so replies get noticed later."*
     This is what keeps a teammate's later answer from being silently missed: the
     find-work sweep can only check threads that are attached to the task.

   Keep it to a few lines. End with something like *"Start by getting oriented, then
   come back to me before doing anything."* so the worker settles into investigate mode
   rather than execution. For a resumed session, no seed is typed at all (step 3).

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

   **iterm2 mode → `spawn.py`.** Opens the worker tab and launches claude with the
   seed as its positional prompt, so claude **auto-submits the seed on startup** — the
   worker begins working on its own, no human Enter needed. (This replaced the old
   type-into-the-box approach, which polled the screen for claude's TUI then typed the
   seed un-submitted; on a slow start that detection timed out, the seed was never
   typed, and the user had to copy-paste it by hand. Passing the seed as the launch
   prompt removes that timing race entirely.) The `--name` titles the tab. The result
   reports `seed`: `"submitted"` (baked into the launch command and auto-submitted) or
   `"skipped"` (no seed — a resume).

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

   - **iterm2 mode:** tell the user the worker tab is open and the seed was
     **auto-submitted**, so the worker is already **getting oriented** — it'll
     investigate the task and then come back to *them* in that tab with its
     understanding, options, and a recommendation before changing anything. So point
     them at the tab to expect that proposal (and steer it), not to watch it execute.
     (No review-then-Enter step anymore.) If `seed` came back `"skipped"` on what
     should have been a fresh spawn, something is off — flag it rather than claiming
     the worker started.

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
