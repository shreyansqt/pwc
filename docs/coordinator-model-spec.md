# Spec: the thin coordinator — routing, identity, and staying free

Design decisions reached with Shreyans (2026-07-15) for how the PWC coordinator
should behave. This is the brief for implementing the skill changes; it is NOT the
implementation. A worker implements from this.

## The core principle

**The coordinator routes and dispatches; it does not do process, and it does not do
execution.** Its job is to hold the whole board in view, decide *what* each task is,
route it to the right skill/model, dispatch a worker, verify the worker is live, and
move on. Everything about *how* a task is done lives in the task's **skill**, not in
the coordinator's seed. The coordinator must stay free — a busy coordinator is a
bottleneck that can't dispatch or track.

## Decisions

### 1. A session becomes "the PWC coordinator" when it runs `/pwc-find-work` or `/pwc-show-work`

These are the coordinator entry points. Because the user starts coordinators in any
directory, the *command* is the trigger, not the location. On becoming coordinator,
the session must:

- **Adopt the coordinator identity + rules** in this spec (a preamble in both skills).
- **Rename its own iTerm tab to `PWC coordinator`** so it's visually unmistakable
  among many tabs. (Concrete action run on skill startup.)
- **Check its model** (see #5) and warn if it's not the best available for its harness.

### 2. Coordinator routes task-type → skill and hands off; the SKILL owns all process

The seed a coordinator writes for a worker becomes thin: *"Your task is `<id>`. Run
`/<skill>` for it — it owns the process."* Plus the durable pointers (load context via
`/pwc-show-task`/`pwc detail`, report via `/pwc-report-status` at the end, attach Slack
threads). **Remove from the seed:** the separate claim-ticket step, the generic
investigate-then-propose gate, and all how-to-do-the-work prose. Those are process, and
process lives in the skill:

- `/start-ticket` already claims the ticket (→ In Progress) as its step 3, then
  researches, then enters plan-mode for approval. The seed must NOT also spell out a
  claim step — that duplicates and can fight the skill.
- `/code-review` already gathers context, posts the 👀 Slack signal (step 1), and gates
  every outward action behind sign-off. The seed must NOT tell the worker to `gh pr
  diff` manually first and defer the skill — that skips the skill's own step 1 (this was
  the observed bug, 2026-07-15).

### 3. Skill present → run it immediately (default). No skill → visible fallback gate.

- **If `skill-hints --type <type>` returns a skill**, the worker runs it **immediately
  as step 1** and trusts the skill's own gates (start-ticket's plan-mode, code-review's
  sign-off, etc.). "Run immediately" is the DEFAULT whenever a skill exists — because
  these skills already contain their own stop-and-ask points. No `run:` config flag is
  needed for the common case.
- **If no skill is configured for the type**, the worker falls back to the generic
  investigate-then-agree gate (investigate freely, change nothing / take no outward
  action until the user agrees an approach) — and start-work **says so explicitly**:
  *"no skill configured for type X — using the generic fallback gate; this is a gap."*
  The gate survives only as a visible, named fallback, never the silent default.

### 4. No-skill task types are a gap the WORKER closes, not the coordinator

When a worker runs a no-skill task type, it does the work the hard way and accumulates
exactly the learnings a skill needs. So **the worker proposes a new skill at wrap-up**
(via `/pwc-report-status` / the workspace skill-review flow) — NOT the coordinator. This
keeps skill-authoring (context-heavy) out of the coordinator, and is self-healing: a
no-skill type generates its own skill proposal the first time it's worked. Encode this
in BOTH start-work's fallback-gate seed ("since there's no skill for this, suggest one
when you're done") and report-status.

### 5. The coordinator runs on the table-selected best model for its harness — start-correct + verify

The coordinator makes the highest-leverage decisions in the system (routing,
trivial-vs-worker, reality reconciliation), so it must run on the strongest available
model for its harness according to PWC's current model table. Do **not** hard-code
model names or versions in the skills. Since a session is already running by the time it
runs find-work, the achievable form is **start-correct + verify**: on becoming
coordinator, the skill checks the current model against the table-derived best
available model for the harness and, if it does not match, **warns the user** to restart
the coordinator on the table-selected model (rather than trying to self-switch
mid-session, which isn't reliably supported). If the harness cannot introspect the
current session model, the coordinator says verification could not be completed and
points at the table-derived target. This is about the COORDINATOR's own model —
distinct from `pwc route`, which sets each TASK's model.

### 6. The coordinator keeps SCOPING discussion; it hands off DESIGNING discussion

The coordinator must stay free of *execution*, not free of *thinking*. A coordinator
that outsources its thinking can't do its job (scoping work IS coordination).

- **Keep:** discussion that decides *what a task is / whether it's worth doing.* That's
  coordination — the coordinator holds the whole-board vantage point that makes it
  productive. Handing this off would lobotomize the role.
- **Hand off:** discussion that has become *how do we build this one thing* — deep design
  of a single artifact, exploring a solution space, drafting. Once the conversation
  shifts from **what** to **how**, the coordinator captures it as a task, profiles it,
  routes it with `pwc route`, and dispatches a worker to continue the discussion there.
  The coordinator must not choose or override the worker harness/model by judgment.
- **The signal to hand off:** the talk has stopped deciding *what* and started designing
  *how*.

### 7. PWC-improvement work is itself worker work

Improvements to PWC (skills, config, this repo) are substantial work and must NOT be done
inline by the coordinator — that blows up its context and blocks dispatch. The
coordinator discusses the improvement with the user (scoping, per #6), captures it as a
task, and dispatches a worker. (This spec is itself an instance: reached by discussion,
handed to a worker to implement.)

### 8. Inline vs. worker line

The coordinator handles **only trivial, seconds-long** things inline (a typo in a task
title, a ref correction, a status flip). Anything that is implementation, a skill/config
change, or a redesign → discuss (scoping), capture as task, dispatch worker. PWC
improvements are always worker work (#7).

## Already shipped (context, not to re-do)

- Routing/session rules in `AGENTS.override.md` at ~/work, smarta, side-projects
  (dispatch on stored routing, resume prior sessions, verify-before-report).
- Slack reaction etiquette in start-work (👀 on dispatch) + report-status (outcome
  emoji on done). NOTE: for `pr-review` tasks, `/code-review` owns the reactions (its
  own 👀/📝/✅, more precise) — so the coordinator's dispatch-👀 should NOT fire for
  pr-review; it stays for non-review slack-bearing task types. (This scoping is part of
  the implementation work.)

## Implementation targets (for the worker)

- `skills/pwc-find-work/SKILL.md` and `skills/pwc-show-work/SKILL.md`: coordinator
  identity preamble (#1), tab rename to `PWC coordinator`, model check+warn (#5),
  scoping-vs-designing handoff rule (#6), PWC-improvement-is-worker-work (#7), inline-vs-
  worker line (#8).
- `skills/pwc-start-work/SKILL.md`: thin-seed rewrite (#2), skill-present-run-immediately
  default + no-skill visible fallback gate (#3), worker-proposes-skill-on-gap (#4), scope
  the dispatch-👀 to skip pr-review (shipped-context note).
- `skills/pwc-report-status/SKILL.md`: worker proposes a skill when the task type had no
  skill (#4).
- Keep changes consistent across all skills; run the test suite (`PYTHONPATH=scripts
  python3 -m pytest tests/ -q`) — these are doc changes, tests should stay green.
