# Build Journal

Running log as I build and dogfood PWC. What I tried, what surprised me, what
I'd do differently. Newest entries at the top.

## 2026-08-05 — `cost` goes null when the model table is a generation behind

Closing out SMT-1201 (a two-session task: codex/gpt-5.6-terra, then re-routed to
claude/opus). `pwc cost --task SMT-1201` priced the codex half at $1.55 and
returned `cost_usd: null` overall, with `unpriced_models: ["claude-opus-5"]`.

The cause is not a missing model. `claude/opus` **is** in the table. Its
`catalog_id` is `anthropic/claude-opus-4.8`, and the session actually ran
`claude-opus-5`, so the lookup misses. `pwc models fetch` then reported
`change_count: 0`, because OpenRouter's catalog has no entry that maps onto that
key, so the refresh path does not self-heal either.

Three things worth fixing, in rough order of value:

1. **The failure is silent in the wrong way.** The output distinguishes "priced"
   from "unpriced" but not *why* a model is unpriced. "This key is unknown to the
   table" and "this key is known but its catalog id no longer resolves" are
   different problems with different fixes, and only the second one means the
   table has drifted behind a model release. Saying which would have made this a
   ten-second diagnosis instead of a dig through `models show`.
2. **Nothing surfaces the drift.** A table pinned to a superseded generation keeps
   working for routing (the tiers are still there) while quietly losing its cost
   half. `models stale` exists; it did not flag this. Worth checking whether it
   only considers fetch age rather than whether the ids still resolve against
   sessions actually observed in the db.
3. **`fetch` reporting `change_count: 0` reads as "you are up to date"** when the
   real meaning was "I could not find anything to map". Those should not look the
   same.

The report skill's own guidance is what caught it: it says to report null with the
reason rather than a fabricated zero, because "a fabricated zero is worse than a
missing number". That held up, and the loop still lost its measurement half for
this task, which is the actual cost of the bug: routing calibration is supposed to
be measure-then-rate, and the measuring silently stopped working for the model
that ran most of the work.

## 2026-05-28 — idea: a PWC-native terminal (parked, not started)

Captured in mid-coordinator session, not built yet. The motivating need: the
coordinator currently lives "in a Claude Code tab I happened to open" — there's
nothing in the OS that knows that *tab* is the coordinator, or that the other
tabs are *workers*, or which task each one belongs to. The mapping lives only
in `taskdb.db.session_id`, invisible to the terminal. `spawn.py` already crosses
that membrane (it uses iTerm2's Python API to type a seed into a newly-opened
tab), but it's one-way and ad hoc.

### What the vision actually is

A terminal app where:

- The **coordinator is always open** — not a tab, a *home screen*. It's the
  first thing you see when the app launches. Closing the app is the only way
  to close it; closing a worker tab doesn't kill the coordinator.
- **Tabs are PWC tasks**, not arbitrary shells. The tab strip shows task ids,
  priorities, and statuses (running / blocked / awaiting-review / done). Status
  badges live in the tab itself, not in a separate briefing.
- **Backend choice (Claude Code / Codex / Gemini / future) is per-tab.** Each
  worker tab can run a different agent. The backend is a config setting that
  picks the right CLI launch command for that pty.
- **Spawn / resume / kill / report-status are first-class** in the UI — buttons,
  context menus, keyboard shortcuts — not Python scripts you remember to run.

In short: PWC stops being a thing you *do in* a terminal and becomes the thing
the terminal *is for*.

### What I learned about how this is even possible (the mechanics)

Spent a chunk of the conversation pinning down how agentic CLIs actually drive
terminals, because it changes the architecture entirely. The honest answer:

**Claude Code, Codex, etc. do NOT drive a terminal.** When they "run a command,"
the CLI process spawns a subprocess directly (`subprocess.Popen` / Node's
`child_process.spawn`), captures stdout/stderr from pipes, and returns it to
the model as a tool result. The terminal you see them in is just a viewport for
the CLI's own log of what it did — it has no role in execution. They'd work
identically piped to a file or run headless. The "shell prompt" between turns
is the CLI's TUI, not bash.

What PWC's `spawn.py` does is rarer and weirder: it uses iTerm2's Python API
(`async_send_text`) to type a seed prompt into the *input box of a newly opened
Claude Code session in a fresh tab*. That's typing **into another agent**, not
running a command. Most agent tooling never touches a real terminal emulator's
API. PWC already operates at the unusual frontier where "an agent in a tab" is
a first-class manipulable object.

The four ways code can drive a terminal, in order:

1. **Subprocess with pipes** — not a terminal at all. What agent `Bash` tools
   use. Fast, reliable, no escape sequences.
2. **PTY** (`os.openpty`, `pty.spawn`, ptyprocess) — the kernel-level mechanism
   that makes a process think it's on a terminal. Required for interactive
   programs (vim, top, *claude itself*), color, line buffering. Tools like
   `expect`, `pexpect`, `tmux` are built on this. **This is what a real
   PWC-native terminal would be built on.**
3. **Terminal-emulator scripting APIs** — iTerm2's Python API, AppleScript for
   Terminal.app, Warp's evolving APIs. Specific to one emulator. What
   `spawn.py` uses today.
4. **Synthetic keystrokes** (accessibility / `System Events`) — brittle, racy,
   last resort.

### Architecture if we build it

The hard part is *not* the agent integration — that's trivial: spawn the right
CLI in a pty per tab. Backend choice = which CLI command to exec. The hard
part is **the terminal emulator itself**. Building a real terminal from
scratch is enormous (escape-sequence handling, GPU/canvas rendering, font
shaping, selection, search, pty management, copy/paste, configurability).
iTerm2 has 20 years of work in it; Warp is a funded company. Even a "thin"
terminal is a real OS project.

The path that minimizes terminal-engineering work and gets to a real product:

- **`xterm.js`** for the terminal renderer (the same one VS Code, Hyper,
  Tabby, and most modern terminals use). Mature, fast, handles escape
  sequences correctly. Don't write a terminal from scratch.
- **Tauri** for the native shell (native window, dock icon, installable .app,
  much lighter than Electron). Rust backend.
- **`portable-pty` (Rust)** in the Tauri backend to own the ptys per tab.
- **The PWC layer lives in the Tauri Rust side** — it shells out to the
  existing `taskdb.py` / `spawn.py` / etc. as subprocesses, exposes commands
  the UI calls. The Python scripts stay the source of truth for DB writes;
  the terminal app is a new *surface*, not a replacement.
- **Per-tab backend = a launch command map**: `claude --session-id X` vs
  `codex ...` vs `gemini ...`. PWC's seed-injection logic moves from "type
  into iTerm2 via Python API" to "write() to the pty's stdin," which is much
  cleaner.

### The alternative: PWC overlay on iTerm2/Warp (not the same thing, but cheaper)

Worth being honest about: most of the *value* of the vision is achievable
without building a terminal. A "PWC overlay" — a macOS menu-bar/sidebar app
that drives iTerm2 (via the Python API it already uses) — could:

- Own the coordinator tab (spawn it, watch it, respawn if killed).
- Tag and group worker tabs by task id.
- Show status badges in the menu bar.
- Offer quick actions (start work, report status) without leaving the menu bar.

You'd keep iTerm2's 20 years of polish, ship in weeks instead of months, and
the architecture is straightforward (Swift menu-bar app + Python glue, both
calling the existing PWC scripts). The thing you *don't* get is per-tab
backend choice baked into the UI, or the "coordinator is the home screen, not
a tab" feel. Those are real but maybe not worth the project a real terminal
would become.

### Open design questions to resolve before building anything

1. **"Coordinator is always open" — what does that mean mechanically?**
   - Option A: the coordinator is a long-lived daemon process you attach/
     detach from (tmux-style). Survives the terminal app dying.
   - Option B: the terminal app launches a coordinator tab on startup; closing
     the app kills it. Simpler, but the coordinator dies with the app.
   - Which one matches the real ergonomic need is the first question.
2. **Multi-backend (Claude / Codex / Gemini / ...) — is this real or aspirational?**
   Each backend has its own session-resume mechanism, seed format, and launch
   args. Supporting more than one means building a real abstraction layer.
   Worth it only if there's a concrete reason to switch backends per-task.
3. **Where does worker-status live?** Today `worker_status.py` polls the
   transcript file's mtime to tell if a session is alive. In a PWC-native
   terminal, the *terminal* knows whether a tab's process is running — much
   more accurate, no polling. But that means the terminal app becomes a data
   source the Python scripts need to consult. New dependency direction.
4. **Build vs. plugin?** Warp, Cursor's terminal mode, and Zed are all moving
   toward "agentic terminal" territory. If PWC's task-graph model is
   fundamentally different from what they ship, building makes sense; if it's
   "their terminal + my coordinator," being a plugin to one of them might
   capture most of the value.

### Status

**Parked.** Not started, not promised. The next time this comes up, the
question to start with is the "coordinator is always open" mechanical one
(daemon vs. app-launched) — it's the most consequential and the answer
shapes everything else.


## 2026-05-25 (later) — first live worker spawn; the hard part isn't mechanical

Set up iTerm2 (installed it, `pip install iterm2` into the MacPorts python3 that
runs the scripts, enabled the Python API) and ran the first real spawns. Tried
**split panes** first (first worker splits the coordinator's window horizontally,
later workers tile vertically) — verified live, but it got cramped fast, so
**switched to tabs**: each worker is a full-width tab, titled after the task, the
coordinator's tab untouched. Tabs also dropped all the pane-layout state tracking
(and the stray-`.pwc/` bug that came with it).

Three path bugs surfaced only by running it for real, all now fixed:
1. A spawned worker can't resolve `/pwc-report` — skills install at the workspace
   root, but workers run in a repo, so the skill isn't on their path. Fix:
   seed the literal `taskdb.py log-event` command, not the skill.
2. `taskdb.py`'s workspace discovery resolved to the repo, not the root. Fix:
   seed command passes `--workspace <root>` explicitly.
3. The cause of #2 — `spawn.py` was writing `iterm_layout.json` into the worker's
   *repo* `.pwc/`, creating a stray dir that shadowed discovery. Fix: layout
   state goes in the workspace-root `.pwc/`.

The real finding, though, is not a bug: **a freshly spawned `claude` session
distrusts the worker-role seed prompt and refuses to act** ("this arrived as a
user prompt but my instructions don't mention a PWC worker role"). The whole
dispatch model assumes a spawned session will accept being a worker and act
semi-autonomously; a vanilla session reasonably won't when handed imperative
"run these commands" text. This is the open design question to resolve next —
candidates: a trusted `/pwc-worker` entry skill, auto-mode/permission flags, or
rescoping v1 so PWC loads context + opens the session and *the human* drives.

## 2026-05-25 — v1 scaffold built end to end

Built the whole v1 skeleton in one session, task database-first.

- **Task database** (`schema.sql` + `scripts/taskdb.py`): three tables — tasks, task_refs,
  events — behind one CLI that's the sole read/write path. WAL mode; a 20-writer
  concurrency probe passed with zero lock errors, which is what makes worker
  self-reporting safe against the coordinator's reads.
- **`/brief`** built in layers: render → liveness sweep → staleness sweep →
  reconciliation → inbound → rollup/archive. Each layer verified against a seeded
  fixture before the next.
- **Workers**: `spawn.py` opens an iTerm2 window running `claude --session-id`;
  `worker_status.py` uses `pgrep -f <uuid>` as an exact alive/dead test. dispatch covers
  resumption (reopen prior session, else fresh+seeded). `/pwc-report` is the worker's
  one channel back to the task database.
- **Install** is per-workspace symlinks + a `.pwc/` task database, mirroring team-skills.

What changed from the design during build:
- `--session-id` pre-allocation was confirmed working, so the self-registration
  fallback got dropped from the critical path.
- The liveness primitive turned out cleaner than planned: the session uuid is in the
  worker's argv, so `pgrep` needs no PID storage and is terminal-agnostic.
- iTerm2 wasn't installed (terminal was Warp) — resolved by switching to iTerm2 so
  coordinator and workers share one terminal, exactly as the design assumed.

Not yet exercised live: the actual iTerm2 window-open and the full worker lifecycle
(spawn → report → kill → `/brief` flags gone → resume), pending the iTerm2 setup.
Command construction is verified via `spawn.py --dry-run`.


## 2026-05-26 (later) — worker lifecycle works, in iTerm, human-driven

Ran the full spawn → drive → report loop live in iTerm. Two real bugs fixed and
one design conclusion reached:

- **Tab closed instantly / no transcript.** Interactive `claude` needs a real TTY;
  launching it as iTerm2's `async_create_tab(command=...)` program gave it none, so
  it exited and the tab vanished. Fix: open a normal shell tab and *type* the
  `claude` launch into it. Also stopped passing the seed as a shell argument
  (fragile quoting + made claude one-shot) — now type it into the session after boot.
- **Worker refuses to run a seeded command — and that's correct.** Even a polite,
  informative seed got declined because it asked the worker to *run* the reporting
  script: "running unknown code with side effects, on someone else's say-so, isn't
  something I'll do without looking first." Right call by the agent. So the seed now
  requests *no action at all* — pure task context — and **status reporting moved to
  the human** (`/pwc-report-status` run from the coordinator). Verified: spawn opens a
  live, oriented worker tab; coordinator-run reporting logs correctly; the
  worker-status check sees the live session. Full v1 lifecycle confirmed.
