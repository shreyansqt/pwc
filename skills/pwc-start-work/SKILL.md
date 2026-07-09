---
name: pwc-start-work
description: Act on a PWC task — either dispatch a worker (a Claude Code session, spawned in its own iTerm2 tab) for substantial work, or handle it inline for trivial work. Also covers resuming a task whose worker has stopped. The default is to dispatch a worker.
---

# /pwc-start-work

Turn a tracked task into action. `/pwc-start-work` decides whether the task warrants
its own **worker** (a Claude Code session, spawned in a new iTerm2 tab) or can be
handled **inline** by the coordinator, then does it. It also covers **resumption** —
there is no separate resume command; picking a stopped task back up is just starting
it again, reopening its prior session when one survives.

A worker is a normal Claude Code session that *you* drive — `/pwc-start-work` opens it
in the right repo with the task's context pre-loaded so you can begin immediately.
It does not coerce the session into acting autonomously; it sets it up and gets out
of the way.

## Configuration

- **CLI**: `pwc` — on PATH (installed by `install.sh` as `~/.local/bin/pwc`). All task-database access goes through it; never read or write the database directly.
- **Workspace root**: the current directory (e.g. `~/work/acme`).
  A task's `workdir` is relative to this (a repo like `service-backend`, or the
  root itself).
- **Spawning requires iTerm2** with the Python API enabled.

## Tools

- `pwc detail --task <id>` — the task's fields, refs, and
  event timeline; the basis for the cwd, the resume decision, the seed prompt, and
  the dispatch target (`harness` + `model`, set at queue time by the routing policy;
  NULL harness = claude, NULL model = the harness's default).
  Read it for *routing* — workdir, whether there's a session to resume, and the refs
  to name in the seed. Do **not** use it as license to go read the linked thread/PR/
  Jira yourself; pulling that underlying content into the coordinator's context is the
  worker's job, not yours. The seed names the refs and the task id; the worker derives
  the substance.
- `pwc worker-status --session-ids <uuid>` — whether the task's existing
  session (if any) is currently running.
- `pwc spawn --task <id> --cwd <dir> [--harness <h>] [--model <m>] --session-id <uuid> [--resume] [--prompt -] [--name "<id> · <gist>"]`
  — open the worker tab and launch the task's harness (default claude) with the seed
  as its prompt, so it **auto-submits the seed on startup** (the worker starts working
  immediately; there is no review-then-Enter step). Prints
  `{harness, model, session_id, session_tracked, cwd, mode, transcript_expected, seed}`
  where `session_tracked` says whether this harness pre-allocates session ids (claude:
  true — it gates `set-session` and everything session-based; see the **Non-claude
  harnesses** note) and `seed` is `submitted`
  (baked into the launch command and auto-submitted) or `skipped` (no `--prompt`
  given). **A `--prompt` is honored on `--resume` too** — `claude --resume <id>
  '<prompt>'` resumes with full history AND auto-submits the prompt, so you can pipe a
  *follow-up* into a resumed worker and it runs on its own (verified 2026-07-07). The
  seed is never typed into the input box, so there is no `not-typed` failure mode and
  nothing for the user to paste by hand.
- `pwc set-session --task <id> --session-id <uuid> --workdir <dir>`
  — record the pre-allocated session id at spawn (atomic with a `dispatched` event).
- `pwc clear-session --task <id>` — the inverse: NULL the
  session_id (logs a neutral note, not a dispatch; status untouched). Use to back out
  a session recorded by mistake, or to detach a finished/abandoned one so the task
  reads as not-dispatched.
- `pwc sources skill-hints [--type <type>]` — the configured
  task-type → skill(s) map. **Run it with `--type <task-type>` while building every
  fresh seed** (step 4): if it returns skills for the type, the seed must strongly
  recommend them. This is the workspace's deliberate routing — e.g. `pr-review` →
  `code-review` — so a PR-review worker always starts from `/code-review`, not from
  whatever tool the coordinator happens to think of. The map is the source of truth
  for "which skill runs this kind of work," not the coordinator's judgement.
- `pwc update-task` / `log-event` — for inline outcomes.

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

   **While you have `detail` open, canonicalize the id to its Jira key if needed.**
   If the task has a Jira key as an identity ref but its canonical `id` is still a
   generated slug (e.g. a `slack-…` task that gained a ticket), `pwc promote
   --task <id> --new-id <KEY>` first, so the worker's seed and the board both use the
   key. Promote keeps the old id as an alias, so the `session_id` you record and any
   later resume still resolve. (Skip if the id already is the key, or there's no Jira
   identity ref.)

3. **Decide fresh vs. resume.** If the task has a `session_id`, check it with
   `pwc worker-status`:
   - **Alive** → the worker already exists. Don't spawn a duplicate; just point the
     user at its tab. Stop.
   - **Dead/gone, and its transcript still exists** → resume: call `pwc spawn` with
     that same `--session-id` and `--resume`. The worker comes back with its full
     prior conversation. **If you're resuming *to hand it a follow-up*** (the common
     case — a teammate replied, the blocker cleared, there's a new ask), pipe that
     follow-up via `--prompt -` just like a fresh seed: `claude --resume` auto-submits
     it, so the resumed worker runs the follow-up on its own with its full history
     intact (result reports `"seed": "submitted"`). Write the follow-up the same way
     you'd write a seed — state the new ask, point at any refs — but you don't need to
     re-explain the task (the worker already carries it). **Resuming with no follow-up**
     (just reopening the tab for the user to continue in) → omit `--prompt`; the result
     reports `"seed": "skipped"`, which is correct, and you tell the user the worker
     resumed with its history for them to continue in the tab. Only fall back to
     "here's text to paste" if `--prompt` delivery fails for some reason.
   - **No live `session_id` on the task → do NOT jump to fresh yet. Look back through
     the event log for a prior session to resume first.** A task's `session_id` is
     *detached* (cleared) every time a worker reports `done`/`blocked`/`note` and its
     tab closes — so a task that was worked, then **blocked, then later unblocked**
     reads as "no session" even though a fully-resumable session with all the prior
     investigation is sitting in its history. This is the common shape for an unblocked
     task (e.g. "tested the endpoint, filed a support ticket, blocked on their reply" →
     reply arrives → resume the worker that did the testing, don't start one that has
     to rediscover it). So before spawning fresh: run `pwc detail --task <id>`,
     scan the `events` for the most recent `dispatched` event's `session_id`, and check
     whether its transcript still exists (`pwc worker-status` for liveness; the
     transcript path is `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`). If a prior
     session's transcript survives, **resume that** (`pwc spawn --session-id <that-id>
     --resume --prompt -`) and pipe the follow-up that nudges it toward the new
     development (the unblock) — it auto-submits, so the resumed worker picks up the
     new ask on its own. Its context is worth far more than a clean slate. Only when there is genuinely no
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
   a raw `pwc …` line it can't verify and shouldn't auto-run.

   **The hard gate: investigate freely, change nothing until the user picks an
   approach.** The worker may read/search/inspect anything — repo, Jira, PR diff,
   Slack threads, logs — to understand the work. But it must **not take any
   state-changing or outward action** until the user has agreed an approach: no writing
   or editing code, no creating branches or PRs, no editing Jira ticket *content*, no
   posting to Slack, no running mutating jobs. Reaching the point where it *could* act
   is the cue to stop and ask, not to proceed. (This gate applies to **all task
   types** — jira, pr-review, slack, project alike.)

   **The one exception — claiming the ticket on pickup.** Moving the linked Jira
   ticket to *In Progress* and assigning it to the user is **bookkeeping that says "I've
   picked this up," not work on the task**, so it is *exempt from the gate* and is the
   worker's **first step**, done *before* any investigation — see the claim-step seed
   part below. The gate still covers every *substantive* Jira edit (changing the
   description, scope, acceptance criteria, status beyond the In-Progress claim, etc.) —
   only the pickup transition + assignment are pre-cleared. **Reviews are the
   counter-exception: a review task must NOT be claimed** (don't transition it, don't
   reassign it) — see the claim-step part for why.

   Build the seed from these parts:

   - **The PWC task id, stated first as the handle** — e.g. *"Your PWC task is
     `SMT-921`."* This is the durable key to everything else.
   - **An instruction to load its own context via `/pwc-show-task <id>`** — e.g. *"Run
     `/pwc-show-task SMT-921` to pull your full context (fields, refs, event timeline)
     from the task DB."* Pass the id with the skill so resolution is trivial (the
     skill's session-inference fallback is for when the id is lost — don't rely on it).
     The worker pulls the *current* fields/refs/timeline itself — no stale snapshot
     baked into the seed, and nothing for the coordinator to transcribe.
   - **The claim step (jira-linked own-work tasks only) — the worker's FIRST action,
     before investigating.** When the task has a linked Jira ticket *and is the user's
     own work to do* (a `jira`-type task — implement / fix / build), instruct the worker
     to **claim the ticket as its very first step**: move it to *In Progress* and assign
     it to the user (Shreyans). Make it **idempotent** — skip the transition if it's
     already In Progress, skip the assignment if it's already assigned to the user; only
     change what isn't already right, and don't reassign away from the user. This is the
     pickup-bookkeeping carve-out from the gate (above), so it happens up front, *not*
     gated behind the approve-the-approach step. Phrase it like: *"First, before
     investigating: claim this ticket — move SMT-921 to In Progress and assign it to me
     if it isn't already (skip whichever is already correct). Then get oriented."*
     - **Do NOT add the claim step for review tasks.** If the task is a *review*
       (reviewing someone else's PR/work — typically a `pr-review` task, or any task
       whose point is to review rather than implement), the worker must **not** claim
       the ticket: don't transition it and don't reassign it. A review ticket is
       normally already assigned to the user *as the reviewer* (see the team's
       "assignee = reviewer when In Review" convention), and claiming it would
       misrepresent who did the work and could steal it from the author. For a review,
       omit the claim part entirely; if a review ticket ever looks like it needs a
       transition/assignment, that's a "stop and ask the user" — never auto-claim.
   - **A one-line statement of the task's goal/intent** — *what* outcome the task is
     after, drawn from the title/notes, so the worker knows the target. This is the
     *only* substantive content the seed carries, and it describes the destination, not
     the route. Do not spell out steps, tools, or sequencing to get there.
   - **The plain-language summary first — the opening move of the report-back.** Before
     any options or recommendation, the worker's first output after investigating must
     be a short **plain-language summary that rebuilds the context**: what the problem
     is, what the idea/task is, what *this specific ticket* is asking for, and the key
     decision(s) at stake — in simple words, no jargon, no code detail. It's a
     *reminder* that re-establishes the shared picture (the user has many tasks in
     flight and may be cold on this one), not a status report. Only after that summary
     come the options and the recommendation. Phrase it in the seed as: *"When you come
     back, lead with a plain-language summary that reminds me what this task is — the
     problem, the idea, what this ticket asks for, and the key decisions — in simple
     words. Then give your options and recommendation."*
   - **The investigate-then-propose-then-ask directive** — phrase it as: *"Investigate
     enough to understand this task, then STOP and come back to me: first the
     plain-language summary above, then your understanding, the approaches you see (with
     trade-offs) and your recommendation, and ask how I'd like to proceed. Don't make
     any change — no code, no branch, no ticket move, no Slack post, no job — until
     we've agreed an approach."* This is the part that puts the *how* decision back with
     the worker + user.
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
   rather than execution. (This whole part builds the *fresh-spawn* seed. For a
   resumed session you don't rebuild the task seed — you either pass a short follow-up
   via `--prompt -` (the new ask; auto-submits) or omit `--prompt` entirely; see
   step 3.)

   **Reporting: at completion, not on startup.** The `/pwc-report-status` ask is
   strictly the *closing* step ("when you're done or blocked") — never "report now" or
   "report at every step." `/pwc-show-work`'s worker-status check already notices a
   vanished session and preserves any status the worker did report, so nothing is lost
   if the worker ends without reporting. The user can also run `/pwc-report-status`
   from the coordinator at any time.

5. **Pre-allocate and dispatch.** For a claude task, generate a UUID and pass it as
   `--session-id` (so the id is known before any process exists). For an opencode
   task, pass no `--session-id` on a fresh spawn — `pwc spawn` pre-creates the
   session itself (via opencode's server API) and returns the minted id; **record
   the result's `session_id`, never one you generated**. Pass the task's
   `--harness` and `--model` when set (from `detail`; omit when NULL — claude with
   its default model). Pipe the seed via `--prompt -`. Pass a
   scannable `--name "<id> · <short gist>"` — the id plus a 3–5 word gist from the
   title (e.g. `SMT-677 · BO auth review`, `slack-ocr · OCR income prefill`).

   `pwc spawn` opens the worker tab and launches claude with the
   seed as its positional prompt, so claude **auto-submits the seed on startup** — the
   worker begins working on its own, no human Enter needed. (This replaced the old
   type-into-the-box approach, which polled the screen for claude's TUI then typed the
   seed un-submitted; on a slow start that detection timed out, the seed was never
   typed, and the user had to copy-paste it by hand. Passing the seed as the launch
   prompt removes that timing race entirely.) The `--name` titles the tab. The result
   reports `seed`: `"submitted"` (a `--prompt` was baked into the launch command and
   auto-submitted — on a fresh spawn this is the task seed, on a resume it's a
   follow-up) or `"skipped"` (no `--prompt` — a bare resume with no follow-up).

6. **Record it, then tell the user how to start the worker.** Right after dispatch,
   run `pwc set-session --task <id> --session-id <result.session_id> --workdir <dir>`
   (writes the session id and a `dispatched` event, so the task is tracked from the
   instant the worker starts) — **only if the spawn result said `session_tracked:
   true`** (claude, opencode). For an untracked harness (codex) there is no session
   to record, so instead log the dispatch (`pwc log-event --task <id> --kind
   dispatched --detail "spawned <harness> (<model>) worker in <dir>"`). Either way,
   **move the task to `in-progress`**
   (`pwc update-task --task <id> --status in-progress`) — dispatching a worker is
   what flips a task from `pending` to `in-progress`. (On a resume of an already-started
   task it's already in-progress; setting it again is harmless.) Then, in your reply:
   tell the user the worker tab is open and the seed was **auto-submitted**, so the
   worker is already **getting oriented** — it'll investigate the task and then come
   back to *them* in that tab with its understanding, options, and a recommendation
   before changing anything. So point them at the tab to expect that proposal (and
   steer it), not to watch it execute. (No review-then-Enter step anymore.) If `seed`
   came back `"skipped"` on what should have been a fresh spawn, something is off —
   flag it rather than claiming the worker started. **On a resume:** if you piped a
   follow-up (`seed: "submitted"`), tell the user the worker resumed with its full
   history and is already working the follow-up in that tab; if you did a bare resume
   (`seed: "skipped"`), tell them it resumed with its history and is waiting for them
   to continue in the tab.

### Inline path

7. **Act directly** via the coordinator's own skills (e.g. `/slack-message`) and
   record the outcome with `pwc log-event --task <id> --kind note --detail
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

- **Non-claude harnesses (task `harness` = opencode, codex, …).** Dispatch works the
  same — build the seed, `pwc spawn --harness <h> [--model <m>]` — with these deltas:
  - **opencode is session-tracked like claude** (verified 2026-07-10): fresh spawns
    pre-create the session via opencode's server API (record the returned
    `session_id`), the liveness check in step 3 works (`pwc worker-status` — the
    `ses_…` id is in the worker's argv), and resume is `pwc spawn --harness opencode
    --session-id <ses_…> --resume` (spawn verifies the session still exists in
    opencode's store; if it's gone it mints a fresh one — check the result's
    `session_id` and mode). Skip only the *transcript-path* check in step 3 — that
    file layout is claude's; spawn handles opencode existence itself. One unverified
    detail: whether `--prompt` auto-submits in the TUI — on the first opencode spawn,
    ask the user to confirm the seed actually ran, and note the answer in the task.
  - **codex is untracked**: no pre-allocated id, so skip step 3's liveness/resume
    logic entirely; picking the task back up is `--resume` (codex reopens that
    directory's most recent session — best-effort). In step 6, log the `dispatched`
    event instead of `set-session`. `/pwc-show-work`'s dead-worker sweep won't see
    this worker — remind the user its status is only what gets reported.
  - **Adapt the seed's PWC references** (all non-claude harnesses): the `/pwc-*`
    skills are Claude Code skills, so the seed points at the `pwc` CLI instead —
    *"Run `pwc detail --task <id>` for your full context"* replaces
    `/pwc-show-task`, and *"record the outcome with `pwc log-event --task <id>
    --source worker --kind note --detail …`"* replaces `/pwc-report-status`.
    Everything else in the seed (the gate, the investigate-then-ask directive) is
    harness-neutral.
  - codex's launch flags are **unverified until first real use** — if a spawn
    errors, check the actual CLI flags against the builders in `spawn.py` and fix
    them there.
- **Never resume a session that's still alive** — that's what the worker-status check in
  step 3 guards against (claude-harness tasks only).
- /start does not auto-pick tasks; it acts on a task the user chose (often via
  `/next`). It assumes the user has confirmed.
- `pwc spawn` does not touch the task database; this skill owns the `set-session` write, so
  all coordinator-side DB writes go through one path.
