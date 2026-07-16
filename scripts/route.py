#!/usr/bin/env python3
"""`pwc route` — pick the model for a task. Deterministic, cheapest-that-qualifies.

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

  3. RANK — cheapest first, by BLENDED cost (see `_blended_cost`). Ties break toward
     the LOWER tier (don't over-serve — an over-tiered pick is wasted money) then by
     key (total determinism: same input, same table -> same answer, always).

NO FALLBACK CHAINS. If nothing qualifies, this exits nonzero and says what filtered
everything out. A failed dispatch is a decision for the user, not something to paper
over by silently downgrading to a model that was already judged unfit — that's how
you get a "cheap" run that quietly produces garbage on a task that needed care.

Every model is priced at RACK RATE, including the ones behind subscriptions
(claude, codex). Pricing a subscription at zero would make the router maximize the
exact spend we're trying to measure, and would hide whether the plan still earns
its keep. See models.py for the full reasoning.

Usage:
  route.py --domain implementation --reasoning 4 [--verifiability 3]
           [--risk none|outward|prod-data] [--context-need 200000] [--explain]
  route.py --json -        # the same profile as a JSON body on stdin

Output (stdout, JSON): the decision — {harness, model, key, why, cost_per_mtok, ...}
plus `candidates` (what else qualified, cheapest-first) when --explain.
"""

from __future__ import annotations

import argparse
import sys

import models as models_mod
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


def route(profile: dict, table: dict) -> dict:
    """Pure decision function: (profile, table) -> decision. No I/O, no globals.

    Kept pure so it is trivially testable and so the same inputs always produce the
    same output — a router you can't predict is one you stop trusting.
    """
    domain = profile["domain"]
    reasoning = int(profile["reasoning"])
    verifiability = int(profile.get("verifiability", 3))
    risk = profile.get("risk", "none")
    context_need = int(profile.get("context_need", 0) or 0)

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
            rejected.append({"key": key, "why": "harness unavailable "
                                                "(not installed or not authenticated)"})
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

    # Cheapest that clears the bar, after cost_weight. Tie-break toward the lower
    # tier (don't over-serve), then the key, so the result is fully deterministic.
    candidates.sort(key=lambda c: (c["blended"] * c["cost_weight"], c["tier"], c["key"]))
    pick = candidates[0]

    bits = [f"cheapest {domain} model at tier >= {need}"]
    if raised_why:
        bits.append(raised_why)
    if context_need:
        bits.append(f"{pick['context']:,} ctx covers the {context_need:,} needed")
    runners = [c for c in candidates[1:3]]
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="route.py", description=__doc__)
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
    p.add_argument("--json", metavar="-", help="read the profile as JSON on stdin")
    args = p.parse_args(argv)

    if args.json == "-":
        profile = read_json_stdin()
    else:
        if not args.domain or args.reasoning is None:
            fail("route: --domain and --reasoning are required "
                 "(or pass the profile with --json -)")
        profile = {"domain": args.domain, "reasoning": args.reasoning,
                   "verifiability": args.verifiability, "risk": args.risk,
                   "context_need": args.context_need}

    table = load_table_for_routing(no_fetch=args.no_fetch)
    decision = route(profile, table)
    if not args.explain:
        decision.pop("candidates", None)
        decision.pop("rejected", None)
    emit(decision)


if __name__ == "__main__":
    main(sys.argv[1:])
