# Adapting PWC for Claude Desktop (no iTerm2 / no terminal)

**Status:** findings + options, pre-implementation. Several open questions need
Stella's input before any code is written.

**Context:** Stella likes PWC and wants to use it, but her setup has **no iTerm2
and no terminal** — she drives Claude from the **Claude Desktop app**. PWC's
worker model (`/pwc-start-work` → `scripts/spawn.py`) hard-depends on iTerm2's
Python API to open a tab and type into it, so it cannot run on her machine as-is.

---

## 1. What actually assumes a terminal / iTerm2

The dependency is **not** spread through PWC — it is concentrated in two scripts
plus the install path. Everything else (the task DB, the briefing/triage/pick
skills) is terminal-agnostic and would work unchanged.

### Hard terminal/iTerm2 dependencies

| Where | What it assumes | Why it breaks on Desktop |
|---|---|---|
| `scripts/spawn.py` | iTerm2 is running with the **Python API** enabled and the `iterm2` pip package is installed. It calls `iterm2.async_get_app`, grabs `current_terminal_window`, `async_create_tab()`, and `async_send_text()` to **launch `claude` in a shell tab and type the seed into its input box**. | No iTerm2 → no window, no tab, no `claude` CLI to launch. The whole spawn path is inert. (`spawn.py` already fails loudly rather than hanging — but it can only fail, never succeed.) |
| `scripts/worker_status.py` | A worker is a **local `claude --session-id <uuid>` process**, so liveness = `pgrep -f <uuid>`. | A Desktop "worker" is not a local CLI process. `pgrep` finds nothing, so every worker reads as dead/gone. The alive/resume branch in `/pwc-start-work` never fires. |
| `/pwc-start-work` (skill) | Calls both of the above, and its whole **fresh-vs-resume + seed-in-input-box** flow is written around an iTerm2 tab a human then presses Enter in. | The entire "worker path" (steps 2–6) is terminal-shaped. The **inline path** (steps 7–8) is not — it's just the coordinator acting directly. |

### Terminal-agnostic (works as-is, no terminal needed)

- `scripts/taskdb.py`, `schema.sql`, `pwc_db.py`, `_common.py` — pure SQLite over a
  CLI. No terminal assumption beyond "something can run `python3`."
- `scripts/sources.py`, `claude_md.py` — config + CLAUDE.md splice.
- Skills `pwc-show-work`, `pwc-show-task`, `pwc-pick-work`, `pwc-find-work`,
  `pwc-triage-slack`, `pwc-report-status` — these read/write the DB and reason;
  none of them needs a terminal. **The coordinator's whole brief/find/triage/pick
  loop is already Desktop-compatible.**

### The install path (separate assumption)

`install.sh` symlinks the skills into **`~/.claude/skills/`** and runs `python3
taskdb.py init`. That directory is the **Claude Code CLI** skills dir. Whether the
**Claude Desktop app** discovers skills there — or anywhere — is **unknown and is
open question Q3 below.** Also: the install script assumes a shell to run it and
`python3` on PATH; a Desktop-only user may have neither in the way we expect.

**One-line summary:** the *coordinator* is already desktop-ready; only the
*worker-spawning* mechanism (and liveness check, and skill install) are
terminal-bound.

---

## 2. The crux: what is a "worker" without a terminal?

PWC's worker = a second Claude Code **process** in its own tab, which the
coordinator launches and the human then drives. That requires (a) a way to start a
second Claude session programmatically, and (b) a way to know it's alive. The
Claude Desktop app gives us neither today: you can't script it to open a new
conversation, and there's no local process to `pgrep`.

So the design question is genuinely about *her workflow*, not just a code port.
The options below differ in **how much of the worker model survives**.

---

## 3. Options

### Option A — Coordinator-only / inline-first (smallest change, most certain)

Drop programmatic worker-spawning for Desktop. The coordinator runs in one Desktop
conversation and does everything **inline** (the existing steps 7–8 of
`/pwc-start-work`). For anything too big to inline, it hands the user a **ready-to-
paste seed** and the user **opens a new Desktop conversation themselves** and pastes
it — a manual stand-in for the tab. The new conversation isn't tracked as a live
process (no `pgrep`), so liveness/resume degrade to "the user tells us / we ask."

- **Pros:** Works today with near-zero new infrastructure. The find/triage/pick/
  brief loop — the bulk of PWC's value — is fully intact. Matches how a Desktop user
  already works (multiple chat windows).
- **Cons:** Loses automatic spawn + automatic liveness/resume. "Workers" become
  manual and untracked-as-process; the DB can't auto-detect a dead worker, so
  status leans more on `/pwc-report-status` and on the user. Two parallel UX models
  to maintain (terminal vs desktop).
- **Code shape:** a `desktop` / `inline-only` mode flag (per-workspace config or
  env) that makes `/pwc-start-work` skip `spawn.py`/`worker_status.py` and instead
  emit the seed as copy-paste text; make `worker_status.py` degrade gracefully
  instead of asserting `pgrep`.

### Option B — Alternate spawn mechanism for Desktop

Replace iTerm2 with a Desktop-compatible way to launch a worker. Candidates, each
with an unknown to verify:
- **macOS Terminal.app / AppleScript / `open`** instead of the iTerm2 Python API —
  *but Stella reportedly has no terminal at all, so this likely just relocates the
  same dependency she doesn't have.*
- **`claude` in headless/print mode** (`claude -p …`) as a background job the
  coordinator launches and reads back — turns "worker" into a non-interactive run,
  not a session she drives. Changes the model significantly.
- **Claude Desktop automation** (URL scheme / deep link / app-level scripting to
  open a new conversation pre-seeded) — *unknown whether the Desktop app exposes
  any such hook.* This is the only candidate that preserves "a session she drives,"
  and it's the biggest unknown.

- **Pros:** Could preserve the real worker model (a drivable session) on Desktop.
- **Cons:** Every candidate has a load-bearing unknown; most either reintroduce a
  terminal or change what a worker *is*. Highest risk, possibly no viable hook
  exists.

### Option C — Hybrid: coordinator-only default + manual-worker escape hatch

Ship Option A as the supported Desktop path, and document the manual "open a new
conversation and paste this seed" flow as the explicit way to run a worker when one
is warranted. Keep `worker_status` honest by having a Desktop worker self-register
via `/pwc-report-status` (or a `set-session` it runs itself) so the DB still tracks
it — just sourced from the worker reporting in, not from `pgrep`.

- **Pros:** Realistic and shippable now; keeps a worker concept without needing a
  spawn API; degrades cleanly. Probably the **recommended** path pending Stella's
  answers.
- **Cons:** Worker tracking depends on the worker (or user) reporting in, not on an
  OS-level liveness signal. Manual paste step is a small friction.

---

## 4. Open questions for Stella (resolve before any code)

These are the three from the task timeline, sharpened by the investigation:

1. **Does she even want workers, or is coordinator-only / inline enough?**
   If her real need is "brief me, find work, triage Slack, suggest what's next, and
   handle small things" — that's already Desktop-ready (Option A core) and we may
   not need spawning at all. If she genuinely wants to fan out parallel sessions,
   we need a spawn answer (B/C).

2. **When a task is too big to inline, how does she want to run it?**
   (a) Coordinator hands her a seed and she opens a new Desktop conversation and
   pastes it (Option A/C), or (b) she expects PWC to open it for her automatically
   (needs Option B — and a Desktop hook we haven't confirmed exists)?

3. **How do PWC's skills install on her setup — does Claude Desktop read skills,
   and from where?** PWC installs skills into `~/.claude/skills/` for the **CLI**.
   We need to confirm: does she use **Claude Code** at all (CLI/IDE), or **only the
   Desktop chat app**? Where (if anywhere) does her client load skills from? This
   determines whether the skills install unchanged, need a different location, or
   need to be delivered some other way (e.g. as plain instructions/files).

**Bonus to confirm with her:** does she have **`python3` and a shell** available at
all? `taskdb.py` and `install.sh` assume both. If not, even the coordinator-only
path needs a delivery rethink (the DB still needs *something* to run the CLI).

---

## 5. Recommendation (pending Stella's answers)

Lead with **Option C** (coordinator-only default + documented manual-worker escape
hatch). It ships on what we know works, preserves nearly all of PWC's value
immediately, and doesn't bet on an unconfirmed Desktop automation hook. Keep Option
B (a real spawn mechanism) open only if Stella's answer to Q1/Q2 is "I specifically
want PWC to launch drivable parallel sessions for me" — and even then, gate it on
first confirming a Desktop hook actually exists.

**Do not write code until Q1–Q3 are answered** — the answers decide whether we
build a mode flag (A/C) or chase a spawn mechanism (B), and Q3 may change how the
whole thing is even delivered to her.
