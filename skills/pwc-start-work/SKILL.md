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
  the dispatch target (`harness` + `model` + `runhost`, set at queue time by
  `pwc route`). Spawn reads these from the task record; NULL means unrouted.
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
  — open the worker tab and launch the task's harness. Harness/model are READ from
  the task record (set by `pwc route` at queue time); do NOT pass `--harness` or
  `--model` in normal use. For a re-scoped task with stale routing, run `pwc
  reroute --task <id>` first (clears the fields so the next spawn refuses and tells
  you to re-route). The locked-down override (rare, logged): `--force-model
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
- `pwc set-session --task <id> --session-id <uuid> --workdir <dir>`
  — record the pre-allocated session id at spawn (atomic with a `dispatched` event).
- `pwc reroute --task <id> [--reason "..."]` — clear a task's
  stored harness/model (for a re-scoped task whose routing is stale from a different
  chapter). The next `pwc spawn` will refuse and hand over a `pwc route` template.
  Does NOT clear the session; those concerns are separate.
- `pwc clear-session --task <id>` — the inverse: NULL the
  session_id (logs a neutral note, not a dispatch; status untouched). Use to back out
  a session recorded by mistake, or to detach a finished/abandoned one so the task
  reads as not-dispatched.
- `pwc sources skill-hints [--type <type>]` — the configured
  task-type → skill(s) map. **Run it with `--type <task-type>` while building every
  fresh seed** (step 4): if it returns skills for the type, the seed tells the worker
  to run the configured skill immediately as step 1. This is the workspace's deliberate
  process routing — e.g. `pr-review` → `code-review` — so a PR-review worker starts
  from `/code-review`, not from whatever tool the coordinator happens to think of. The
  map is the source of truth for "which skill owns this kind of work." If it returns
  no skills, the seed names that as a visible process gap and uses the generic
  fallback gate.
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

4. **For a fresh session, build a thin seed that routes to the task-type skill.** The
   coordinator's seed says what task this is, points at durable context, and hands off
   process ownership. It does **not** claim tickets, design an approach, list
   implementation steps, or choose a different harness/model. The worker's harness and
   model are the task's stored `harness`/`model` from `pwc detail`, which were set by
   `pwc route` when the task was queued; `/pwc-start-work` dispatches those stored
   values and does not override them by judgment.

   Run `pwc sources skill-hints --type <task-type>` before writing every fresh seed.

   **If a skill is configured, the seed tells the worker to run it immediately as step
   1.** The skill owns the process and its gates: `/start-ticket` owns claiming,
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
     clear, run `/pwc-report-status` for this task."*
   - **The attach-threads step** — *"If you post to or meaningfully read any Slack
     thread about this task, attach it to the task as a working ref (via
     `/pwc-report-status` / `add-ref`, using the message's real `thread_ts`) so replies
     get noticed later."*

   **If no skill is configured, say so explicitly and use the generic fallback gate.**
   Phrase it plainly: *"No skill is configured for task type `<type>` — using the
   generic fallback gate; this is a process gap."* Then tell the worker to load
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
   run `pwc set-session --task <id> --session-id <result.session_id> --workdir <dir>`
   (writes the session id and a `dispatched` event, so the task is tracked from the
   instant the worker starts) — **only if the spawn result said `session_tracked:
   true`** (true for all current harnesses: claude, opencode, codex). For a future
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
   none — skip silently).

   Then, in your reply:
   tell the user the worker tab is open and the seed was **auto-submitted**. If a
   task-type skill was configured, the worker is already running that skill; its own
   gates govern when it stops for approval or sign-off. If no skill was configured,
   the worker is using the visible generic fallback gate and will investigate, then ask
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
- **Non-claude harnesses (task `harness` = opencode, codex, …).** Dispatch works the
  same — build the seed, `pwc spawn` (harness/model come from the task record, not
  CLI flags) — with these deltas:
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
  - **A future untracked harness** (spawn result `session_tracked: false`): skip
    step 3's liveness/resume logic, and in step 6 log the `dispatched` event
    instead of `set-session`; the worker's status is then only what gets reported —
    remind the user of that.
  - **Adapt the seed's PWC references** (all non-claude harnesses): the `/pwc-*`
    skills are Claude Code skills, so the seed points at the `pwc` CLI instead —
    *"Run `pwc detail --task <id>` for your full context"* replaces
    `/pwc-show-task`, and *"record the outcome with `pwc log-event --task <id>
    --source worker --kind note --detail …`"* replaces `/pwc-report-status`.
    The configured-skill vs. fallback-gate split is harness-neutral.
- **Never resume a session that's still alive** — that's what the worker-status check in
  step 3 guards against (claude-harness tasks only).
- /start does not auto-pick tasks; it acts on a task the user chose (often via
  `/next`). It assumes the user has confirmed.
- `pwc spawn` does not touch the task database; this skill owns the `set-session` write, so
  all coordinator-side DB writes go through one path.
