---
name: pwc-start-work
description: Act on a PWC task — either dispatch a worker (a Claude Code session, spawned in its own iTerm2 tab) for substantial work, or handle it inline for trivial work. Also covers resuming a task whose worker has stopped. The default is to dispatch a worker.
---

# /pwc-start-work

Turn a tracked task into action. `/pwc-start-work` decides whether the task warrants
its own **worker** (a Claude Code session, spawned in a new iTerm2 tab) or can be
handled **inline** by the coordinator, then does it. It also covers **resumption** when you are dispatching a specific task: picking a
stopped task back up reopens its prior session when one survives. From inside a
workspace, with no task name, `pwc resume` lists every session on that board and
`pwc continue` opens the most recent one.

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
  the dispatch target. Routing happens AT SPAWN TIME: the task carries a
  `route-profile` event (saved at queue time by `pwc route … --save`), and `pwc
  spawn` routes that profile against the live subscription quota. A `harness` +
  `model` on the task record is a PIN (or the queue-time route of a task queued
  before 13.10.2026) and wins over the profile. `runhost` is unchanged.
- `pwc sources runhosts` — the named remote machines workers can run on
  (`{name: {ssh, workspace_root, …}}`, or `{}`). Resolve a task's `runhost` here
  to get the `--ssh` target and to map the task's `workdir` onto the REMOTE
  workspace root (`<workspace_root>/<workdir>` is the remote cwd).
  Read it for *routing* — workdir, whether there's a session to resume, and the refs
  to name in the seed. Do **not** use it as license to go read the linked thread/PR/
  Jira yourself; pulling that underlying content into the coordinator's context is the
  worker's job, not yours. The seed names the refs and the task id; the worker derives
  the substance.
- `pwc worker-status --session-ids <uuid>` — whether the task's existing
  session (if any) is currently running.
- `pwc spawn --task <id> --cwd <dir> [--ssh <target> --runhost <name>] --session-id <uuid> [--resume] [--prompt -] [--name "<id> · <gist>"]`
  — open the worker tab and launch the task's harness. Spawn DECIDES the
  harness/model itself, in this order: the session's own harness on `--resume`; a
  pin on the task record; otherwise `pwc route` on the saved profile with live quota
  (logged as a `routed` event, and returned as `routed` in the result). Do NOT pass
  `--harness` or `--model` in normal use, and never pick them yourself. For a
  re-scoped task, save a new profile with `pwc route … --save`; to remove an old
  pin, run `pwc reroute --task <id>`. On the first spawn of the day, spawn runs
  `pwc maintain` (harness updates, model discovery) before it routes. The locked-down override (rare, logged): `--force-model
  --force-reason "<why>" --harness <h> --model <m>`. The seed is passed as the
  harness's prompt, so it **auto-submits on startup**. Prints
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
- `pwc set-session --task <id> --session-id <uuid> --workdir <dir> --cwd <dir>
  --harness <h> --model <m>` — record the session id at spawn (atomic with a
  `dispatched` event). **Always pass `--harness`/`--model`/`--cwd` from the spawn
  result.** With spawn-time routing the task record no longer holds the harness,
  and a resume reads the harness and the directory that ran the session from this
  record. `--cwd` is the spawn result's `cwd` (the remote path, when there is one).
- `pwc reroute --task <id> [--reason "..."]` — clear a task's
  stored harness/model PIN. The next `pwc spawn` routes the saved profile; with no
  profile it refuses and hands over a `pwc route … --save` template.
  Does NOT clear the session; those concerns are separate.
- `pwc clear-session --task <id>` — the inverse: NULL the
  session_id (logs a neutral note, not a dispatch; status untouched). Use to back out
  a session recorded by mistake, or to detach a finished/abandoned one so the task
  reads as not-dispatched.
- `pwc sources skill-hints [--type <type>]` — the configured
  task-type → skill(s) map. A task can also carry its own skill: the newest event
  with `kind` `skill` on `pwc detail`. **Read that event first while building every
  fresh seed** (step 4). If it names skills, the seed tells the worker to run the
  first one immediately. Find-work writes this event when a label in the map names
  the work more specifically than the task type (a Product Refinement stays type
  `calendar` and carries `refinement-prep`). If there is no skill event, run
  `pwc sources skill-hints --type <task-type>`. If that returns skills, the seed
  tells the worker to run the first one immediately. This is the workspace's
  deliberate process routing — e.g. `pr-review` → `code-review` — so a PR-review
  worker starts from `/code-review`, not from whatever tool the coordinator happens
  to think of. If the event and the type map are both empty, the seed names that as
  a visible process gap and uses the generic fallback gate.
- `pwc sources claim [--type <type>]` — the workspace's claim policy per task type:
  a `check` (is the work already taken?) and `steps` (the claim actions). Read it in
  step 3c, before every fresh dispatch. `{}` means this type has nothing to claim.
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

2. **Resolve the owning workspace and working directory.** Start from the target
   repo, not the coordinator's current directory. Find its nearest ancestor that
   contains `.pwc/store.json` or `.pwc/taskdb.db`; that is the task's owning PWC
   workspace. Use the task's `workdir` (from `detail`) joined to that workspace root.
   If absent, infer it from refs (e.g. a PR's repo) or ask the user.

   A local `pwc spawn` resolves the task store from `--cwd`. Before dispatch, verify
   that `pwc detail --workspace <owning-workspace> --task <id>` finds the task. If the
   target repo belongs to a different sibling workspace, create the task in that
   workspace and pin every task write with `--workspace <owning-workspace>`. Do not
   create a convenience copy in the coordinator's current workspace. If a task was
   already created on the wrong board, recreate it with its refs on the correct board,
   then archive the misplaced row with a reason.

   This exact cwd must be reused verbatim on any later resume — the session transcript
   is keyed by it.

   If a `--resume` fails with *"no transcript for session …"*, that keying is why:
   the session ran under a different cwd (a renamed workspace, or a task whose
   board and transcript disagree). Find the slug that actually holds
   `<session-id>.jsonl` under `~/.claude/projects/` and spawn with a matching
   `--cwd`, or move the transcript to this cwd's slug. **Do not "fix" it by
   dropping `--resume`** — that starts cold and silently abandons the context you
   were trying to keep.

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

3b. **Do NOT research the task before dispatching.** The thin-seed rule below governs
   what the seed may CONTAIN; this one governs what the coordinator may DO to write it.
   They are different, and complying with the first while breaking the second is the
   common failure.

   Before dispatching, the coordinator may read: the PWC task (`pwc detail`), the
   ticket or issue the task points at, and the routing/skill-hint config. That is all
   it needs.

   It must NOT: grep or read source, check whether a referenced module/client/table
   actually exists, verify a ticket's technical claims, inspect repo or git state,
   query external systems for supporting detail, or otherwise pre-establish facts the
   worker will establish anyway.

   Three reasons, in order of weight:
   - **It pollutes the coordinator's context.** The two-tier design exists so the
     coordinator holds the shape of ALL work rather than the detail of one task. Every
     file read to write a seed spends the budget that keeps the board legible.
   - **It is slower, not faster.** The worker re-derives it regardless, with better
     tools and full attention on one task.
   - **It makes findings look authoritative when they are cheap.** A coordinator's
     five-minute grep, stated in a seed, reads to the worker as established fact. If it
     is wrong, the worker inherits the error with the coordinator's confidence behind
     it.

   The pull is real: pre-checking FEELS diligent, especially on a task with production
   consequences. Resist it. Dispatch, and let the worker report. If a task genuinely
   cannot be scoped without investigation, that IS the task — dispatch it as an
   investigation rather than doing the investigation and dispatching the conclusion.

   The narrow exception is DISPATCH MECHANICS, not task content: confirming a prior
   session's transcript exists and which cwd it is keyed to, and checking whether a
   runhost is reachable. Those decide HOW to spawn, not WHAT the work is.

3c. **Claim the work BEFORE a fresh dispatch.** Run `pwc sources claim --type
   <task-type>`. It returns the workspace's claim policy for this task type: a `check`
   (how to see that someone else already took the work) and `steps` (the claim
   actions). How work is claimed is workspace policy, so the skill names no ticket
   system or chat tool here.

   - **Empty result (`{}`) → nothing to claim. Go to step 4.** The workspace has no
     claim convention for this task type.
   - **The coordinator claims, even when the task type's skill also can.** A worker
     reaches its skill's claim step minutes after the spawn. Until then the team's
     board shows the work as free, and a teammate can pick it up. So the claim happens
     here, before the spawn, and the seed tells the worker it is done (see below).
   - **Run the `check` first. If someone else took the work, STOP.** Do not claim, do
     not spawn. Tell the user who holds it and since when, and let them decide: leave
     it, take it over, or archive the task. A takeover is the user's call, never the
     coordinator's — overwriting a teammate's claim is visible to the whole team.
   - **If the work is free, run every `steps` entry, then dispatch.** A claim step
     that fails stops the dispatch: report it, because an unclaimed review that two
     people then do is the cost this step exists to remove.
   - **Tell the worker the claim is done**, as one environment line in the seed
     ("Already done by the coordinator: …"), so its skill does not claim twice.
   - **A resume runs this step too.** A task that comes back (reopened, handed back,
     unblocked) is often unclaimed again: the ticket sits in its to-do column, or
     someone else holds it now. The `check` says when the claim already holds; then
     skip the `steps`. Claim steps are safe to repeat.

   The reads this step needs (the ticket's assignee and status, the reactions and
   replies on the request) are dispatch mechanics, not task research: they decide
   WHETHER to spawn. They do not license reading the diff or the ticket's content.

   (18.09.2026: the older of two review requests looked free on the board. The ticket
   was In Review and assigned to a teammate since the day before. Only a manual check
   before the spawn caught it; the skill had no claim step, and its 👀 came after the
   dispatch.)

4. **For a fresh session, build a thin seed that routes to the task's skill.** The
   coordinator's seed says what task this is, points at durable context, and hands off
   process ownership. It does **not** claim tickets, design an approach, list
   implementation steps, or choose a different harness/model. `pwc spawn` decides the
   worker's harness and model (see the spawn tool above); `/pwc-start-work` never
   overrides that by judgment. **Learn the harness before you write the seed**: run
   `pwc spawn --task <id> --cwd <dir> --dry-run` first. It routes exactly as the real
   spawn will (the quota reading is cached for 5 minutes) and launches nothing. Write
   the seed for the harness it returns (see **Non-claude harnesses**), then spawn for
   real within those 5 minutes. If the real spawn returns a different harness, stop
   and rewrite the seed before the user relies on the worker.

   Read the newest event whose `kind` is `skill` before writing every fresh seed.
   Its `detail` is one skill name, or a JSON list of skill names. That list is the
   skill handoff for this task. Do not also look up the type.

   If there is no `skill` event, run `pwc sources skill-hints --type <task-type>`.

   **If a skill is configured, the seed tells the worker to run it immediately as step
   1.** The skill comes from the event, or from the type map when the event is
   absent. The skill owns the process and its gates: `/start-ticket` owns claiming,
   research, and plan approval; `/code-review` owns review context, Slack signaling, and
   sign-off; any future skill owns its own workflow. Do not duplicate those steps in
   the seed and do not insert a generic investigate-then-propose gate before the skill.
   The worker should trust the configured skill's stop-and-ask points.

   Build the configured-skill seed from these parts:

   - **The PWC task id, stated first as the handle** — e.g. *"Your PWC task is
     `SMT-921`."*
   - **The immediate skill handoff** — e.g. *"Run `/start-ticket` for this task now; it
     owns the process."* If multiple skills are returned, name them in the returned
     order and tell the worker to start with the first unless that skill explicitly
     routes onward.
   - **The durable context pointer** — for Claude Code workers, *"Run
     `/pwc-show-task SMT-921` when you need the task fields, refs, and event timeline."*
     For non-Claude harnesses, use the CLI form from the notes below.
   - **A one-line goal/intent** from the task title/notes, describing the desired
     outcome without prescribing how to get there.
   - **The closing-report step** — *"When you've finished or hit a blocker you can't
     clear, run `/pwc-report-status` for this task."* For a non-Claude harness the
     seed must spell out BOTH halves of what that skill does, because the worker
     cannot run it: log the outcome, then **set the status** — *"record the outcome
     with `pwc log-event --task <id> --source worker --kind note --detail …`, then
     `pwc update-task --task <id> --status done` (or `blocked`) as your last
     action."* A seed that only asks for the note leaves the task `in-progress`
     forever: on 15.09.2026 eight codex workers finished, each logged a thorough
     note, and none flipped its status, so the board showed eight in-flight tasks
     with one live worker.
   - **The attach-threads step** — *"If you post to or meaningfully read any Slack
     thread about this task, attach it to the task as a working ref (via
     `/pwc-report-status` / `add-ref`, using the message's real `thread_ts`) so replies
     get noticed later."*

   **If no skill is configured, say so explicitly and use the generic fallback gate.**
   This applies only when the task has no `skill` event and the type map is empty
   for its type. Phrase it plainly: *"No skill is configured for task type `<type>`
   — using the generic fallback gate; this is a process gap."* Then tell the worker to load
   context, investigate freely, and stop before any state-changing or outward action
   until the user agrees an approach: no code edits, branch, PR, substantive Jira
   change, Slack post, or mutating job. Since there is no skill for this type, also
   tell the worker to propose at wrap-up whether the repeated workflow should become a
   new skill or instruction.

   Keep either seed to a few lines. For a resumed session, don't rebuild the task seed;
   pass only the new follow-up via `--prompt -`, or omit `--prompt` for a bare resume
   (see step 3).

   **Reporting: at completion, not on startup.** The `/pwc-report-status` ask is
   strictly the *closing* step ("when you're done or blocked") — never "report now" or
   "report at every step." `/pwc-show-work`'s worker-status check already notices a
   vanished session and preserves any status the worker did report, so nothing is lost
   if the worker ends without reporting. The user can also run `/pwc-report-status`
   from the coordinator at any time.

5. **Pre-allocate and dispatch.** For a claude task, generate a UUID and pass it as
    `--session-id` (so the id is known before any process exists). For an opencode or
    codex task, pass no `--session-id` on a fresh spawn — `pwc spawn` pre-creates the
    session itself (via the harness's server API) and returns the minted id; **record
    the result's `session_id`, never one you generated**. Do NOT pass `--harness` or
    `--model` — spawn reads them from the task record. Pipe the seed via `--prompt
    -`. Pass a scannable `--name "<id> · <short gist>"` — the id plus a 3–5 word gist
    from the title (e.g. `SMT-677 · BO auth review`, `slack-ocr · OCR income
    prefill`).

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
   run `pwc set-session --task <id> --session-id <result.session_id> --workdir <dir>
   --cwd <result.cwd> --harness <result.harness> --model <result.model>`
   (writes the session id and a `dispatched` event, so the task is tracked from the
   instant the worker starts) — **only if the spawn result said `session_tracked:
   true`** (true for all current harnesses: claude, opencode, codex, grok,
   cursor-agent, agy). For a future
   untracked harness there is no session to record, so instead log the dispatch
   (`pwc log-event --task <id> --kind dispatched --detail "spawned <harness>
   (<model>) worker in <dir>"`). Either way, **move the task to `in-progress`**
   (`pwc update-task --task <id> --status in-progress`) — dispatching a worker is
   what flips a task from `pending` to `in-progress`. (On a resume of an already-started
   task it's already in-progress; setting it again is harmless.)

   **Then mark the task's Slack thread(s) as picked-up — add 👀.** For each Slack ref
   on the task (identity AND working `slack` refs from `detail`), react to the thread's
   root message with **`eyes`** (`slack_add_reaction`), so the teammate who posted the
   review/ask sees it's been picked up without anyone typing a reply. Get `channel_id`
   and `message_ts` by parsing the stored permalink — `/archives/<channel_id>/p<digits>`
   → `channel_id` is the `C…` segment, `message_ts` is the digits with a `.` inserted
   6 from the end (`p1783622525430479` → `1783622525.430479`). **Best-effort:** a
   reaction that fails (thread gone, bad ref) is logged and skipped, never a reason to
   fail the dispatch. **Additive only:** Slack has no remove-reaction API, so 👀 is
   *added*, never later swapped — the worker adds an *outcome* emoji next to it at the
   end (see report-status), and 👀 simply stays. **Known limitation (deliberate):** a
   task later paused/killed keeps its 👀 — there's no removal API and this is
   best-effort by design; don't try to build teardown. Only react at all when the task
   actually carries Slack refs (smarta tasks do; a GitHub-only side-projects task has
   none — skip silently). When step 3c's claim already put 👀 on a message, skip that
   message here; this step still covers the task's other Slack refs, and task types
   with no claim entry.

   Then, in your reply:
   tell the user the worker tab is open and the seed was **auto-submitted**. If a
   skill was named (a `skill` event on the task, or a skill for the task type), the
   worker is already running that skill. Its own gates govern when it stops for
   approval or sign-off. If no skill was named, the worker is using the visible
   generic fallback gate and will investigate, then ask
   before changing anything. (No review-then-Enter step anymore.) If `seed`
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

- **Remote workers (task `runhost` set).** The worker runs on that machine inside
  a **remote tmux session** — it survives the laptop sleeping and the tab closing;
  the iTerm tab is just a viewport. Deltas from a local dispatch:
  - Resolve the runhost via `pwc sources runhosts`: pass `--ssh <its ssh>` and
    `--runhost <name>` to spawn, and pass `--cwd` as the **remote** path
    (`<workspace_root>/<task workdir>`). Run any `pre` step the runhost config
    names (e.g. checking Tailscale is up) before spawning.
  - Because `--cwd` names a directory on the *other* machine, it cannot locate
    the task's store. Spawn resolves that from the local invocation directory,
    so either run it from inside the task's workspace or pass
    **`--local-root <local workspace dir>`**. Dispatching from a PARENT of
    several workspaces, always pass it — the parent is not the task's workspace.
  - **claude harness only for now** — spawn rejects opencode/codex remotely
    (their session pre-allocation would have to run on the remote host).
  - Everything session-based works: same pre-allocated uuid, liveness via
    `pwc worker-status --json -` with `"ssh"` in the row, resume via the same
    spawn call with `--resume` (spawn checks the remote transcript itself).
  - The spawn result includes `attach_command` — give it to the user as the way
    to reopen the worker's viewport if they close the tab (`ssh -t <host> tmux
    attach -t pwc-<task>`); closing the tab does NOT stop a remote worker, say so.
  - **First dispatch into a directory claude has never seen on that host stops at
    the folder-trust prompt** ("Yes, I trust this folder") before the seed runs —
    tell the user their first action in the tab is pressing Enter once, or
    pre-trust the repo dirs during runhost setup.
  - Remote workers can't reach this machine's task database, MCP connectors, the
    VPN-gated prod DB, or a browser — route repo-centric work there, keep
    prod-data investigation local.
- **Non-claude harnesses (opencode, codex, grok, cursor-agent, agy, …).** Dispatch works the
  same — build the seed, `pwc spawn` (spawn decides the harness/model, never CLI
  flags) — with these deltas:
  - **opencode and codex are session-tracked like claude** (both verified
    2026-07-10): fresh spawns pre-create the session via the harness's own server
    API (**record the returned `session_id`** — these harnesses mint their own ids),
    the liveness check in step 3 works (`pwc worker-status` — the id is in the
    worker's argv), and resume is `pwc spawn --harness <h> --session-id <id>
    --resume` (spawn verifies the session still exists in the harness's store; if
    it's gone it mints a fresh one — check the result's `session_id` and mode).
    Skip only the *transcript-path* check in step 3 — that file layout is claude's;
    spawn handles the other harnesses' existence checks itself. Seed auto-submit:
    **opencode's TUI auto-submits the typed seed** (spawn result `seed: "typed"`
    still runs with no manual Enter — verified by Shreyans 2026-07-20), so treat
    `typed` as submitted for opencode. For codex this is still unverified — on its
    first spawn, ask the user to confirm the seed actually ran, and note the
    answer in the task.
  - **grok (Grok Build) is session-tracked like claude** (verified 13.10.2026, grok
    1.0.52): spawn chooses the uuid (`-s <uuid>`), the seed is the positional
    prompt, and resume is `-r <uuid> "<follow-up>"`. Known limits:
    - Grok loads AGENTS.md, project instructions and skills only in a TRUSTED
      folder. When the worker folder is not trusted, the spawn result carries a
      `warning`. Tell the user to answer Grok's trust question in the new tab.
    - Grok does not follow the `@import` in `~/.claude/CLAUDE.md`. Name the
      instruction files by path in the seed (the personal
      `~/workspaces/side-projects/dotfiles/agents/AGENTS.md`, plus the workspace
      `AGENTS.md` / `CLAUDE.md`).
    - Grok bundles its own `code-review` skill, which collides with a workspace
      skill of the same name. Name a workspace skill by its file path in the seed.
    - Some MCP servers do not connect inside Grok (Atlassian failed on 13.10).
  - **cursor-agent (Cursor CLI) is session-tracked like codex** (verified
    21.10.2026, cursor-agent 2026.10.01): a fresh spawn mints the chat with
    `cursor-agent create-chat` (no model turn, no quota), so **record the returned
    `session_id`**. Fresh and resume both launch `--resume <id> "<prompt>"`, and
    the prompt auto-submits. It runs with `--auto-review`: a Cursor classifier runs
    safe tool calls and asks in the tab for the rest. It uses the same X pool as
    grok. Known limits:
    - An untrusted folder stops at Cursor's trust question, and the seed waits
      until somebody answers. The spawn result then carries a `warning`. Tell the
      user to press `a` in the new tab.
    - Cursor loads the folder's AGENTS.md and CLAUDE.md. It does not see the
      personal or pwc skills (`~/.cursor/skills` holds only Cloudflare skills on
      21.10.2026). Name each skill the seed needs by its SKILL.md path.
  - **agy (Antigravity CLI) is session-tracked like codex**: agy mints the
    conversation id, so a fresh spawn runs one tiny headless setup turn first and
    returns the id (**record the returned `session_id`**). It runs with
    `--mode accept-edits`: it edits files freely and asks in its tab before shell
    commands. CodexBar cannot read its quota, so route gives it only a small share.
    When an agy worker stops with a quota error, run `pwc quota mark-empty
    --subscription google-ai --why "<error>"`, then tell the user. Do not move the
    task to another harness by yourself.
  - **Any harness that hits its quota**: the same rule. Mark the plan empty with
    `pwc quota mark-empty` when CodexBar cannot see it, and report to the user.
  - **A future untracked harness** (spawn result `session_tracked: false`): skip
    step 3's liveness/resume logic, and in step 6 log the `dispatched` event
    instead of `set-session`; the worker's status is then only what gets reported —
    remind the user of that.
  - **Adapt the seed's PWC references** (all non-claude harnesses): the `/pwc-*`
    skills are Claude Code skills, so the seed points at the `pwc` CLI instead —
    *"Run `pwc detail --task <id>` for your full context"* replaces
    `/pwc-show-task`, and *"record the outcome with `pwc log-event --task <id>
    --source worker --kind note --detail …`, then `pwc update-task --task <id>
    --status done|blocked`"* replaces `/pwc-report-status`. **Both commands, every
    time.** `/pwc-report-status` writes the note AND the status; a seed that
    translates only the note half leaves every finished codex task `in-progress`
    (observed 15.09.2026, eight tasks). The configured-skill vs. fallback-gate
    split is harness-neutral.
- **Never resume a session that's still alive** — that's what the worker-status check in
  step 3 guards against (claude-harness tasks only).
- /start does not auto-pick tasks; it acts on a task the user chose (often via
  `/next`). It assumes the user has confirmed.
- `pwc spawn` does not touch the task database; this skill owns the `set-session` write, so
  all coordinator-side DB writes go through one path.
