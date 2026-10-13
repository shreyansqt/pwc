#!/usr/bin/env python3
"""`pwc route` — pick the model for a task. Deterministic qualification and preference.

Input is a TASK PROFILE (what the work needs), not a model name — that inversion is
the point. The caller describes the job; the table decides who serves it. Add a row
for a new model tomorrow and it competes for work immediately, with nothing else in
PWC changing. That's what "model-agnostic" buys.

The decision, in order:

  1. HARD FILTERS — a model that fails any of these is not a candidate at all:
     - `available`: the harness is installed AND authenticated. An unavailable model
       would fail at dispatch, and there are NO FALLBACK CHAINS here (see below), so
       it must never be picked in the first place.
     - `context`: the model's window must hold the task's context need.
     - `risk=prod-data`: TWO independent guards, because "capable enough" and
       "allowed to see real data" are different questions and collapsing them is a
       bug (we shipped it once and caught it: a prod-data task routed straight to a
       third-party metered API purely because it cleared the tier floor). So:
         (a) `data_ok` must be true — a privacy judgment you declare per model in the
             table, NOT something inferred from price or capability; and
         (b) tier >= _PROD_DATA_MIN_TIER — real data means a mistake is expensive,
             so don't economize on capability either.

  2. CAPABILITY GATE — keep models whose tier in the task's DOMAIN meets the
     required tier: `reasoning`, raised by one when `verifiability` is low. The
     verifiability adjustment is the honest part of the model: if you can cheaply
     check the work (tests pass / it compiles / the diff is small), a weaker model is
     a fine bet, because a bad answer is caught immediately and costs only a retry.
     If you CAN'T cheaply check it (a research memo, an architecture call, anything
     whose wrongness is silent), the cost of a plausible-but-wrong answer is high, so
     demand more capability up front.

  3. FENCES — two relational rules that need no history:
     - `allowed_harnesses` (workspace policy, e.g. a client workspace that must not
       send code to a new vendor) rejects every other harness.
     - REVIEWER != AUTHOR: a code-review task whose profile names `author_vendor`
       rejects models from that vendor. A model is blind to its own failure modes,
       so a same-family review is worth less (pwc#5, goal 2).

  4. QUOTA (when the caller passes `quota`, see quota.py) — the decision moves from
     "which model" to "which SUBSCRIPTION", because a quota belongs to a plan:
     - a plan that is `exhausted` is rejected;
     - a plan whose quota is `unknown` gets a small deterministic share of tasks
       (`unknown_share`, by task-id bucket) so it still carries work;
     - otherwise the plan with the most unused quota wins (see `_pick_subscription`);
     - inside the winning plan, the cheapest qualified model wins (step 5).
     The capability gate in step 2 is never relaxed to satisfy a quota.

  5. COST — rank by BLENDED cost (see `_blended_cost`). Ties break toward the LOWER
     tier, then by key. Without quota data (legacy cost mode), a domain PREFERENCE
     may first select another qualified model by task-id bucket.

NO FALLBACK CHAINS. If nothing qualifies, this exits nonzero and says what filtered
everything out. A failed dispatch is a decision for the user, not something to paper
over by silently downgrading to a model that was already judged unfit — that's how
you get a "cheap" run that quietly produces garbage on a task that needed care.

Every model is priced at RACK RATE, including the ones behind subscriptions
(claude, codex). Pricing a subscription at zero would make the router maximize the
exact spend we're trying to measure, and would hide whether the plan still earns
its keep. See models.py for the full reasoning.

Usage:
  route.py --task pwc-12 --domain implementation --reasoning 4 [--verifiability 3]
           [--risk none|outward|prod-data] [--context-need 200000] [--explain]
  route.py --json -        # the same profile as a JSON body on stdin

Output (stdout, JSON): the decision — {harness, model, key, why, cost_per_mtok, ...}
plus `candidates` (what else qualified, cheapest-first) when --explain.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone

import models as models_mod
import quota as quota_mod
from _common import emit, fail, read_json_stdin

# A task touching production data gets a capability floor regardless of how simple
# it looks. The downside of a cheap model being wrong on real data is not symmetric
# with the money saved.
_PROD_DATA_MIN_TIER = 4

# An outward-facing task (a message a human will read, a public comment) needs a
# model that writes like a person. Floors the ops-comms tier specifically.
_OUTWARD_MIN_OPS_TIER = 3

# Below this verifiability, demand one extra tier of capability (see module docs).
_LOW_VERIFIABILITY = 2

RISKS = ("none", "outward", "prod-data")

# What a typical agentic task's token mix looks like, used to turn four separate
# per-Mtok prices into ONE comparable number. These weights are a deliberate,
# stated assumption — not a measurement of your workload — and they matter: a live
# PWC worker session showed 32M cache-read tokens against 30k input, so a naive
# in+out comparison would rank models by a price class that is ~1% of the real bill.
# `pwc cost` reports what tasks ACTUALLY cost; this is only for ordering candidates.
_MIX = {"cache_read": 0.80, "cost_in": 0.05, "cost_out": 0.10, "cache_write": 0.05}

_PREFERENCE_BUCKETS = 10_000

# Share of tasks (0-1) that may go to a plan whose quota CodexBar cannot read. Small,
# because nobody can see that plan running dry until a worker hits the wall.
_UNKNOWN_SHARE = 0.10

# Vendor of a model, from its catalog id prefix, then from its harness. A row may
# set `vendor` directly (Antigravity serves Claude models, so harness is not enough).
_CATALOG_VENDOR = {"anthropic": "anthropic", "openai": "openai", "x-ai": "xai",
                   "xai": "xai", "google": "google"}
_HARNESS_VENDOR = {"claude": "anthropic", "codex": "openai", "grok": "xai",
                   "cursor-agent": "cursor", "agy": "google"}


def vendor_of(row: dict) -> str | None:
    if row.get("vendor"):
        return row["vendor"]
    prefix = (row.get("catalog_id") or "").split("/")[0]
    return _CATALOG_VENDOR.get(prefix) or _HARNESS_VENDOR.get(row.get("harness"))


def _blended_cost(row: dict) -> float:
    """One USD-per-Mtok number for ranking, weighted by a typical agentic token mix.

    A model missing a cache price (several OpenRouter models don't publish one)
    falls back to its input price for that class rather than being treated as free —
    a missing price must never make a model look cheaper than one that disclosed it.
    """
    price = models_mod.price_of(row)
    fallback = price["cost_in"]
    total = 0.0
    for field, weight in _MIX.items():
        value = price[field]
        if not value and field in ("cache_read", "cache_write"):
            value = fallback
        total += value * weight
    return round(total, 6)


def required_tier(reasoning: int, verifiability: int) -> tuple[int, str | None]:
    """The tier a model must hit, and why it was raised (if it was)."""
    if verifiability <= _LOW_VERIFIABILITY and reasoning < 5:
        return reasoning + 1, (
            f"raised to {reasoning + 1}: verifiability {verifiability} is low, so a "
            f"wrong answer wouldn't be caught cheaply")
    return reasoning, None


def _preference_bucket(task_id: str) -> int:
    """Stable 0-9999 bucket. The same task always gets the same route decision."""
    digest = hashlib.sha256(task_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % _PREFERENCE_BUCKETS


def _preference_for(table: dict, domain: str,
                    now: datetime | None = None) -> tuple[dict | None, str | None]:
    pref = (table.get("preferences") or {}).get(domain)
    if not pref:
        return None, None
    if not isinstance(pref, dict) or not pref.get("key"):
        fail(f"malformed preference for {domain}: expected an object with `key`")
    try:
        strength = int(pref.get("strength"))
    except (TypeError, ValueError):
        fail(f"malformed preference for {domain}: strength must be 0-100")
    if not 0 <= strength <= 100:
        fail(f"malformed preference for {domain}: strength must be 0-100")
    pref = {**pref, "strength": strength}
    expires_at = pref.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            fail(f"malformed preference for {domain}: expires_at must be RFC3339")
        if expiry.tzinfo is None:
            fail(f"malformed preference for {domain}: expires_at needs a timezone")
        current = now or datetime.now(timezone.utc)
        if current >= expiry:
            return None, f"preference for {pref['key']} expired at {expires_at}"
    return pref, None


def _rack_rate_comparison(preferred: dict, cost_pick: dict) -> str:
    """Explain the real rack-rate difference without treating preference as cost."""
    preferred_cost = preferred["blended"]
    cost_cost = cost_pick["blended"]
    if preferred_cost == cost_cost:
        return "the blended rack rates are equal"
    if cost_cost < preferred_cost:
        cheaper = (preferred_cost - cost_cost) / preferred_cost * 100
        return (f"{cost_pick['key']} is {cheaper:.1f}% cheaper on blended rack rate "
                f"({cost_cost:g} vs {preferred_cost:g})")
    cheaper = (cost_cost - preferred_cost) / cost_cost * 100
    return (f"{preferred['key']} is {cheaper:.1f}% cheaper on blended rack rate "
            f"({preferred_cost:g} vs {cost_cost:g})")


def _pick_subscription(by_sub: dict[str, list[dict]], quota: dict, settings: dict,
                       task_id: str) -> tuple[str, str]:
    """Choose the plan for this task among plans with a qualified, unexhausted model.

    Returns (subscription, why). `by_sub` maps plan -> its qualified candidates.

    - UNKNOWN plans get `unknown_share` of tasks by task-id bucket (deterministic).
    - Among readable plans: with a `fill_order`, the first listed plan that is not
      ahead of its pace (slack >= 0) wins, so cheap plans fill to pace before an
      expensive one gets work. Without it, the plan with the largest slack wins
      (the most quota that would otherwise be wasted at reset).
    - Ties go to the plan whose cheapest candidate is cheaper, then by name.
    """
    known = {s: quota[s] for s in by_sub if quota.get(s, {}).get("status") == "ok"}
    unknown = sorted(s for s in by_sub if s not in known)
    share = float(settings.get("unknown_share", _UNKNOWN_SHARE))
    bucket = _preference_bucket(f"quota:{task_id}")
    if unknown and (not known or bucket < share * _PREFERENCE_BUCKETS):
        pick = unknown[bucket % len(unknown)]
        why = (f"{pick} quota is unknown; it takes {share:.0%} of tasks and task "
               f"{task_id} fell in that share" if known else
               f"{pick} quota is unknown and no readable plan qualifies")
        return pick, why

    def cheapest(s):
        return min(c["blended"] * c["cost_weight"] for c in by_sub[s])

    def slack(s):
        return known[s].get("slack") or 0.0

    for s in settings.get("fill_order") or []:
        if s in known and slack(s) >= 0:
            return s, (f"{s} is first in the fill order that is not ahead of pace "
                       f"(weekly slack {slack(s):+g} points)")
    pick = sorted(known, key=lambda s: (-slack(s), cheapest(s), s))[0]
    others = ", ".join(f"{s} {slack(s):+g}" for s in sorted(known) if s != pick)
    return pick, (f"{pick} has the most unused quota (weekly slack {slack(pick):+g} "
                  f"points" + (f"; {others}" if others else "") + ")")


def route(profile: dict, table: dict, *, now: datetime | None = None,
          quota: dict | None = None,
          allowed_harnesses: list[str] | None = None) -> dict:
    """Pure decision function: (profile, table, quota) -> decision. No I/O.

    Kept pure so it is trivially testable and so the same inputs always produce the
    same output — a router you can't predict is one you stop trusting. Quota is an
    ARGUMENT (the caller reads CodexBar), so route stays pure even though the
    decision now depends on live usage. `quota=None` is the old cost-only mode.
    """
    domain = profile["domain"]
    task_id = str(profile.get("task_id") or "").strip()
    reasoning = int(profile["reasoning"])
    verifiability = int(profile.get("verifiability", 3))
    risk = profile.get("risk", "none")
    context_need = int(profile.get("context_need", 0) or 0)
    author_vendor = profile.get("author_vendor")

    if not task_id:
        fail("route requires a task id for deterministic preference selection "
             "(pass `--task <id>` or JSON field `task_id`)")
    if domain not in models_mod.DOMAINS:
        fail(f"unknown domain {domain!r} — known: {', '.join(models_mod.DOMAINS)}")
    if risk not in RISKS:
        fail(f"unknown risk {risk!r} — known: {', '.join(RISKS)}")
    if not 1 <= reasoning <= 5:
        fail(f"--reasoning must be 1-5 (got {reasoning})")

    need, raised_why = required_tier(reasoning, verifiability)
    if risk == "prod-data" and need < _PROD_DATA_MIN_TIER:
        need = _PROD_DATA_MIN_TIER
        raised_why = (f"raised to {_PROD_DATA_MIN_TIER}: task touches production "
                      f"data, so a cheap mistake is an expensive mistake")

    rejected = []
    candidates = []
    for row in table["models"]:
        key = row.get("key")
        tier = (row.get("tiers") or {}).get(domain)

        if not row.get("available"):
            rejected.append({"key": key, "why": (
                "retired: the harness no longer lists it" if row.get("retired") else
                "turned off in the table" if row.get("note") else
                "harness unavailable (not installed or not authenticated)")})
            continue
        if allowed_harnesses is not None and row.get("harness") not in allowed_harnesses:
            rejected.append({"key": key,
                             "why": f"harness {row.get('harness')} is not allowed in "
                                    f"this workspace (routing.allowed_harnesses)"})
            continue
        if (author_vendor and domain == "code-review"
                and vendor_of(row) == author_vendor):
            rejected.append({"key": key,
                             "why": f"same vendor ({author_vendor}) as the author "
                                    f"of the work under review"})
            continue
        if risk == "prod-data" and not row.get("data_ok"):
            rejected.append({"key": key,
                             "why": "not cleared for production data "
                                    "(set `data_ok` in the table/overlay to allow)"})
            continue
        if context_need and (row.get("context") or 0) < context_need:
            rejected.append({"key": key,
                             "why": f"context {row.get('context'):,} < needed "
                                    f"{context_need:,}"})
            continue
        if tier is None:
            rejected.append({"key": key, "why": f"no tier recorded for {domain}"})
            continue
        if tier < need:
            rejected.append({"key": key,
                             "why": f"{domain} tier {tier} < required {need}"})
            continue
        if risk == "outward":
            ops = (row.get("tiers") or {}).get("ops-comms", 0)
            if ops < _OUTWARD_MIN_OPS_TIER:
                rejected.append({"key": key,
                                 "why": f"outward-facing: ops-comms tier {ops} < "
                                        f"{_OUTWARD_MIN_OPS_TIER}"})
                continue
        candidates.append({**row, "tier": tier, "blended": _blended_cost(row),
                           "cost_weight": row.get("cost_weight", 1.0)})

    if not candidates:
        fail("no model qualifies for this task profile "
             f"(domain={domain}, required tier={need}, risk={risk}, "
             f"context need={context_need:,}). Rejected: "
             + "; ".join(f"{r['key']} — {r['why']}" for r in rejected)
             + ". There is no fallback chain by design: widen the profile, fix "
               "harness availability, or lower the requirement deliberately.")

    # Cost order remains independent of preference. Preference may select another
    # entry from this already-qualified list, but it never adds an entry to it.
    candidates.sort(key=lambda c: (c["blended"] * c["cost_weight"], c["tier"], c["key"]))

    quota_result = None
    if quota is not None:
        subs = quota_mod.subscriptions(table)
        by_sub: dict[str, list[dict]] = {}
        for c in candidates:
            sub = quota_mod.subscription_for_harness(c["harness"], subs) \
                or f"harness:{c['harness']}"
            st = quota.get(sub) or {"status": "unknown"}
            if st.get("status") == "exhausted":
                rejected.append({"key": c["key"],
                                 "why": f"{sub} out of quota: {st.get('why', '')}"})
                continue
            by_sub.setdefault(sub, []).append({**c, "subscription": sub})
        if not by_sub:
            fail("every qualified model's subscription is out of quota. Rejected: "
                 + "; ".join(f"{r['key']} — {r['why']}" for r in rejected)
                 + ". There is no fallback chain by design: wait for a reset, or "
                   "pin the task to a harness deliberately.")
        sub, quota_why = _pick_subscription(by_sub, quota, table.get("quota") or {},
                                            task_id)
        candidates = by_sub[sub]
        quota_result = {
            "subscription": sub,
            "why": quota_why,
            "snapshot": {s: {k: v for k, v in (quota.get(s) or {}).items()
                             if k in ("status", "slack", "why")}
                         for s in by_sub},
        }

    cost_pick = candidates[0]
    pick = cost_pick

    pref, inactive_why = (None, None) if quota is not None else \
        _preference_for(table, domain, now=now)
    preference_result = None
    preference_why = inactive_why
    if pref:
        bucket = _preference_bucket(task_id)
        selected = bucket < pref["strength"] * 100
        preferred = next((c for c in candidates if c["key"] == pref["key"]), None)
        applied = bool(selected and preferred)
        if applied:
            pick = preferred
        preference_result = {
            "key": pref["key"],
            "strength": pref["strength"],
            "task_bucket": round(bucket / 100, 2),
            "selected": selected,
            "applied": applied,
        }
        for field in ("expires_at", "note"):
            if pref.get(field):
                preference_result[field] = pref[field]
        if preferred is None:
            preference_why = (f"preference for {pref['key']} did not apply because "
                              "that model was not in the qualified candidate set")
        elif not selected:
            preference_why = (f"preference target is {pref['strength']}%; task "
                              f"{task_id} bucket {bucket / 100:.2f}% fell outside it")
        elif preferred["key"] == cost_pick["key"]:
            preference_why = (f"preference selected {preferred['key']} for {domain} "
                              f"at {pref['strength']}%; it also won on cost")
        else:
            preference_why = (f"preference selected {preferred['key']} for {domain} "
                              f"at {pref['strength']}% over cost winner "
                              f"{cost_pick['key']}; "
                              f"{_rack_rate_comparison(preferred, cost_pick)}")

    bits = []
    if quota_result:
        bits.append(quota_result["why"])
    if pick["key"] == cost_pick["key"]:
        bits.append(f"cheapest {domain} model at tier >= {need}"
                    + (f" in {quota_result['subscription']}" if quota_result else ""))
    else:
        bits.append(f"qualified {domain} model at tier >= {need}")
    if preference_why:
        bits.append(preference_why)
    if raised_why:
        bits.append(raised_why)
    if context_need:
        bits.append(f"{pick['context']:,} ctx covers the {context_need:,} needed")
    runners = [c for c in candidates[1:3]] if pick["key"] == cost_pick["key"] else []
    if runners:
        parts = []
        for c in runners:
            w = c["cost_weight"]
            parts.append(f"{c['key']}" + (f" (eff ×{w})" if w != 1.0 else ""))
        bits.append(f"beat {', '.join(parts)} on effective cost")
    weighted = {c["key"]: c["cost_weight"] for c in candidates
                if c["cost_weight"] != 1.0}
    if weighted:
        bits.append("cost weights: " + ", ".join(
            f"{k} ×{w}" for k, w in weighted.items()))

    result = {
        "key": pick["key"],
        "task_id": task_id,
        "harness": pick["harness"],
        "model": pick["model"],
        "domain": domain,
        "tier": pick["tier"],
        "required_tier": need,
        "blended_cost_per_mtok": pick["blended"],
        "cost_per_mtok": {f: pick.get(f) for f in models_mod.PRICE_FIELDS},
        "context": pick["context"],
        "why": "; ".join(bits),
        "candidates": [
            {"key": c["key"], "tier": c["tier"], "blended": c["blended"]}
            for c in candidates
        ],
        "rejected": rejected,
    }
    if pick["cost_weight"] != 1.0:
        result["cost_weight_applied"] = pick["cost_weight"]
    if preference_result:
        result["preference"] = preference_result
    if quota_result:
        result["quota"] = quota_result
    return result


def load_table_for_routing(no_fetch: bool = False):
    """The decision table, auto-refreshed if stale. Written as a separate function so
    it's testable without launching the whole CLI.

    Never hard-fails: if the table is stale and the refresh fails (offline, etc.), it
    prints a loud warning and proceeds with the stale table — a routing decision that
    might fail at dispatch is better than refusing to route at all.
    """
    raw = models_mod.load_raw()
    freshness = models_mod.table_freshness(raw)
    if freshness["stale"]:
        if no_fetch:
            print(f"pwc: WARNING — model table is stale ({freshness['why']}); "
                  f"routing may pick non-existent models", file=sys.stderr)
        else:
            print(f"pwc: model table is stale ({freshness['why']}); refreshing...",
                  file=sys.stderr)
            try:
                new, changes = models_mod.compute_fetch(raw)
                models_mod.save(new)
                print(f"pwc: refreshed — {len(changes)} change(s) applied",
                      file=sys.stderr)
                raw = new
            except SystemExit:
                print(f"pwc: WARNING — refresh failed; proceeding with stale table",
                      file=sys.stderr)
    merged = models_mod.merged_models(raw)
    return {**raw, "models": merged}


def allowed_harnesses_for(workspace_root) -> list[str] | None:
    """The workspace's `routing.allowed_harnesses` list from sources.json, or None."""
    if workspace_root is None:
        return None
    import sources
    allowed = (sources._load(workspace_root).get("routing") or {}) \
        .get("allowed_harnesses")
    return list(allowed) if allowed else None


def decide(profile: dict, *, workspace_root=None, no_fetch: bool = False,
           use_quota: bool = True) -> dict:
    """Load the table, the live quota and the workspace fence, then route.

    The I/O shell around the pure `route()`. `pwc route` and `pwc spawn` both call
    this, so the decision at spawn time is exactly what `pwc route` would print.
    """
    table = load_table_for_routing(no_fetch=no_fetch)
    quota = quota_mod.read(table) if use_quota else None
    return route(profile, table, quota=quota,
                 allowed_harnesses=allowed_harnesses_for(workspace_root))


def main(argv=None):
    p = argparse.ArgumentParser(prog="route.py", description=__doc__)
    p.add_argument("--task", dest="task_id",
                   help="stable task id used for deterministic preference selection")
    p.add_argument("--domain", choices=models_mod.DOMAINS,
                   help="what KIND of work this is")
    p.add_argument("--reasoning", type=int,
                   help="1-5: how much reasoning the task genuinely needs")
    p.add_argument("--verifiability", type=int, default=3,
                   help="1-5: how cheaply a wrong answer would be caught "
                        "(low = demand more capability)")
    p.add_argument("--risk", choices=RISKS, default="none",
                   help="none | outward (a human reads it) | prod-data (real data)")
    p.add_argument("--context-need", type=int, default=0,
                   help="tokens of context the task needs the model to hold")
    p.add_argument("--explain", action="store_true",
                   help="include the full candidate/rejection list")
    p.add_argument("--no-fetch", action="store_true",
                   help="skip the automatic table refresh when stale (offline mode)")
    p.add_argument("--author-vendor",
                   choices=sorted(set(_CATALOG_VENDOR.values())
                                  | set(_HARNESS_VENDOR.values())),
                   help="code-review only: the vendor whose model wrote the work; "
                        "its models are excluded from the review")
    p.add_argument("--no-quota", action="store_true",
                   help="ignore live subscription quota (old cost-only mode)")
    p.add_argument("--workspace",
                   help="workspace root whose routing.allowed_harnesses applies "
                        "(default: discovered from the cwd)")
    p.add_argument("--save", action="store_true",
                   help="store this profile on the task (a route-profile event); "
                        "`pwc spawn` routes it again with live quota at spawn time")
    p.add_argument("--json", metavar="-", help="read the profile as JSON on stdin")
    args = p.parse_args(argv)

    if args.json == "-":
        profile = read_json_stdin()
    else:
        if not args.task_id or not args.domain or args.reasoning is None:
            fail("route: --task, --domain and --reasoning are required "
                 "(or pass the profile with --json -)")
        profile = {"task_id": args.task_id, "domain": args.domain,
                   "reasoning": args.reasoning,
                   "verifiability": args.verifiability, "risk": args.risk,
                   "context_need": args.context_need}
        if args.author_vendor:
            profile["author_vendor"] = args.author_vendor

    from _common import find_workspace_root
    root = args.workspace or find_workspace_root()
    # With --save, check the profile WITHOUT quota: a profile that no model can serve
    # must fail now, at queue time, but a plan that is empty right now must not stop
    # a task from being queued. Spawn applies the quota later.
    decision = decide(profile, workspace_root=root, no_fetch=args.no_fetch,
                      use_quota=not (args.no_quota or args.save))
    if args.save:
        import json
        import spawn
        if spawn._load_task_row(str(root), profile["task_id"]) is None:
            fail(f"--save: no task {profile['task_id']!r} in workspace {root}")
        saved = {k: v for k, v in profile.items() if k != "task_id"}
        spawn._log_task_event(str(root), profile["task_id"], spawn.PROFILE_EVENT,
                              json.dumps(saved, sort_keys=True))
        decision["saved_profile"] = saved
        decision["_note"] = ("profile saved. The decision above ignores quota and is "
                             "only a capability check; `pwc spawn` routes again "
                             "with the live quota")
    if not args.explain:
        decision.pop("candidates", None)
        decision.pop("rejected", None)
    emit(decision)


if __name__ == "__main__":
    main(sys.argv[1:])
