# Spec: make model/harness selection un-biasable — spawn refuses unrouted

Design decisions reached with Shreyans (2026-07-15). Implement from this; this is the
brief, not the implementation. A worker implements it.

## The problem

Even after the routing rules were written into instructions, coordinator **bias in model
selection kept leaking in**. Concrete instance this session: a coordinator dispatched
two re-scoped tasks (SMT-1017, SMT-1129) on `claude` — not by running `pwc route`, but by
passively carrying the `harness: claude` field left on the task from an *earlier, different
chapter*. It looked defensible in hindsight, which is exactly the trap: any path where the
coordinator *can* supply or hand-pick a model is a path where bias enters.

**The fix is structural, not behavioral.** As long as `pwc spawn` accepts a
coordinator-filled `--harness`/`--model`, no instruction can fully prevent bias. Remove the
possibility: routing must come from `pwc route` (stored on the task), and spawn must refuse
to run without it.

## Decisions

### 1. `pwc spawn` uses the task's stored harness/model; hand-picking is a locked-down override

- On spawn, read `harness` and `model` **from the task record** (set by `pwc route` at
  route/queue time) and use exactly those. This is the ONLY normal path.
- The `--harness` / `--model` flags do NOT remain as ordinary optional inputs. They survive
  only as a **locked-down override**: gated behind an explicit `--force-model` flag AND a
  required `--force-reason "<why>"`. Using the override logs a loud, auditable event
  (`log-event kind=note` "FORCED model X over routed Y: <reason>"). No silent default, no
  bare `--model` that quietly wins. Absent `--force-model`, passing `--model`/`--harness`
  is an error ("use --force-model + --force-reason to override routing, or run `pwc route`").

### 2. Missing routing → hard error that hands over the fix

- If the task has no `harness`/`model` stored, `pwc spawn` **exits nonzero** and refuses to
  spawn. It must not fall back to a default model or the coordinator's judgment.
- The error message is actionable: it prints a **ready-to-run `pwc route` template**,
  pre-filled with what spawn can infer — map the task's `type` to a `--domain`
  (e.g. `pr-review`→`code-review`, `jira`/`build-or-feature-work`→`implementation`,
  `slack`→`ops-comms`, research/investigation→`research-writing`), and leave the
  judgment flags (`--reasoning`, `--verifiability`, `--risk`) as placeholders for the
  coordinator to fill. So routing is one edit-and-paste away.
  Example emitted:
  `pwc route --task <id> --domain implementation --reasoning <1-5> --verifiability <1-5> --risk none`
  then: "store the result with `pwc update-task --task <id> --harness <h> --model <m>`, then re-run spawn."

### 3. Re-scoped tasks: a separate `pwc reroute` command (NOT folded into clear-session)

- A task re-scoped into a new chapter can carry **stale routing** from the old chapter —
  the SMT-1017/1129 bug. But clearing the session (`clear-session`) should NOT silently
  wipe routing too; keep those concerns separate and explicit.
- Add **`pwc reroute --task <id>`**: clears the stored `harness`/`model` (and logs why), so
  the next `pwc spawn` hits the decision-2 error and forces a fresh `pwc route`. This is the
  deliberate command a coordinator runs when a task changes character (test→ticket,
  review→staging-test, research→implementation). It does NOT itself pick a model — it just
  invalidates the stale one and requires re-routing.

## Why this shape

The coordinator's model-selection judgment is removed from the hot path entirely:
- Normal spawn: model comes from the task (put there by `pwc route`). Coordinator can't bias.
- No routing: spawn refuses and tells you to route. Coordinator can't default.
- Override: possible but loud + reasoned. Coordinator can't do it silently.
- Re-scope: `reroute` forces fresh routing. Stale routing can't ride a chapter change.

There is no remaining path where a model gets chosen by coordinator judgment without either
`pwc route` or an explicit, logged `--force-reason`.

## Implementation targets (for the worker)

- `scripts/spawn.py`: read harness/model from the task; error-with-route-template when
  absent (decision 2); gate `--harness`/`--model` behind `--force-model` + `--force-reason`
  with an audit log-event (decision 1). `pwc spawn` is located by `--cwd` and has no
  `--workspace` (unchanged) — but it DOES need to read the task record, so confirm how it
  resolves the task's workspace (via `--cwd` → workspace root) to load harness/model.
- New `pwc reroute` subcommand (likely in `taskdb.py`): clear harness/model on a task + log
  a `note` event; print a hint to run `pwc route` next (decision 3).
- Update `skills/pwc-start-work/SKILL.md`: the dispatch section no longer tells the
  coordinator to pass `--harness`/`--model` on spawn — instead: profile → `pwc route` →
  store on task (`update-task`) → `pwc spawn` (which reads them). For a re-scoped task, run
  `pwc reroute` first. Document the override (`--force-model` + reason) as the rare exception.
- Tests: spawn refuses when unrouted (asserts nonzero + the route template in stderr); spawn
  uses stored routing when present; `--force-model` requires `--force-reason` and logs;
  `reroute` clears the fields.
- Run the suite (`PYTHONPATH=scripts python3 -m pytest tests/ -q`).

## Also in this task: cost-sensitivity in the router (a per-model cost weight)

Separate but related routing-quality work, folded in here (Shreyans, 2026-07-15).

**Problem:** `route` picks the cheapest model that clears the tier floor, ranking on the
table's raw `cost_in`/`cost_out`. But raw list price under-captures real cost. Observed:
`glm-5.2` (impl tier 4, cost_in 0.924 / cost_out 2.904) kept getting picked for tier-4
work and ran up noticeably more spend than expected, while `deepseek-v4-pro` (tier 3,
cost_in 0.435 / cost_out 0.87) is far cheaper. glm-5.2 is **capable** — the user does NOT
want it downgraded in tier or blacklisted — they want the router to **prefer cheaper models
more strongly**, using pricier ones only when genuinely needed.

**Build a per-model cost weight (preference multiplier), not a blacklist.**
- Add an optional per-model `cost_weight` (default 1.0) in the table **overlay** (user
  calibration — so `pwc models fetch` can't overwrite it). A weight > 1.0 makes a model
  *rank* as more expensive than its list price; < 1.0 makes it cheaper. It does NOT change
  the tier floor or eligibility — a weighted model still wins when it's the only qualified
  option; it just loses ties and near-ties to genuinely cheaper models.
- `route` applies the weight when ranking: effective_cost = list_cost × cost_weight. Keep
  the *reported* cost/why honest — show both the routed pick and that a weight was applied
  (e.g. "glm-5.2 effective ×1.5 → deepseek-v4-pro wins at tier 3").
- Provide a way to set it: `pwc models set-weight --model <m> --weight <w>` (or extend the
  existing tier-overlay setter), writing to the overlay.
- Seed glm-5.2 with a weight that reflects the user's experience (they'll tune it) — but
  the mechanism is the deliverable, not one hardcoded number. Do NOT bake a specific
  glm-5.2 penalty into `route`'s code; it lives in the overlay data.
- Note the interaction with tiers: this is deliberately a *cost* lever, orthogonal to the
  *capability* tier. The user chose cost-weight over tier-demotion precisely because
  glm-5.2 is capable — don't "fix" it by lowering its tier.

Tests: a weighted model loses to a cheaper qualified peer at the same tier; a weighted
model still wins when it's the only one clearing the tier floor; weight lives in the
overlay and survives a `fetch`.

## Related (context, already in flight)

- `pwc-thin-coordinator` (in progress): the broader "coordinator routes, skill owns process"
  redesign. This spec is the *enforcement* half — it makes the routing rule un-bypassable at
  the tool layer. Keep the two consistent (both say: coordinator never hand-picks a model).
- `pwc-fix-codex-model-availability` (in progress): fixes the model TABLE (bad availability
  flags). Orthogonal — that's about which models the table offers; this is about spawn
  refusing to bypass routing.
