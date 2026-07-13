#!/usr/bin/env python3
"""The model table — what models exist, what they cost, what they're good at.

The table is GLOBAL (~/.config/pwc/model-table.json), not per-workspace: which
models exist and what they cost is a fact about the world, and how good each is at
what is a fact about this person's taste. Every workspace routes off the same one.
This script is the single read/write/refresh path to it; `route.py` reads it to
decide, `cost.py` reads it to price a session, /pwc-find-work refreshes it.

Two kinds of column, and the split is the whole design:

  OBJECTIVE (fetched, overwritten on every refresh) — cost per Mtok (four of them:
    in, out, cache-read, cache-write), context window, whether the model is
    reachable at all. These come from OpenRouter's free /api/v1/models endpoint
    (no auth) and from probing which harnesses are actually installed + authed.

  SUBJECTIVE (yours, NEVER fetched) — the capability tiers: how good is this model,
    1-5, at each domain (code-review, implementation, research-writing, ops-comms).
    Seeded once as a starting hypothesis, then owned by you. Corrections live in a
    separate top-level `overlay` object keyed by model key, so a refresh CANNOT
    clobber them: `fetch` rewrites `models[]` wholesale and never touches `overlay`.
    Reads deep-merge overlay over the row. That structural separation is what makes
    "your calibration survives refreshes" true by construction rather than by promise.

Every model is priced at its API RACK RATE — there is deliberately no
subscription/"it's free, I already pay for it" column. Claude and Codex ride
subscriptions today, but pricing them at zero would make the router maximize the
very spend we want to see, and hide whether the plan is still worth its price. So
the router sorts on real cost, and `pwc cost --report` shows what each harness
actually consumed — which is the number that tells you whether to downgrade a plan.
(Caveat stated where it's shown: for subscription harnesses that figure is
fair-value-at-rack-rate, not an invoice.)

A model is identified by three DIFFERENT strings, and conflating them breaks
dispatch:
  key        — "opencode/glm-5.2"          stable identity, what the overlay keys on
  model      — "openrouter/z-ai/glm-5.2"   EXACT string passed to the harness's --model
  catalog_id — "z-ai/glm-5.2"              what OpenRouter's catalog calls it (join key)
Each harness spells the same model its own way (claude wants `opus`; codex wants
`gpt-5.5`; opencode wants a provider-qualified `openrouter/z-ai/glm-5.2` — note
`z-ai`, not `zai`). Verified live 2026-07-13; do not guess these, read them from
`opencode models` / the harness's own docs.

Usage:
  models.py show [--available]     # the merged table (overlay applied)
  models.py stale [--days 7]       # {"stale": bool, "age_days": N, ...} — the refresh clock
  models.py fetch [--dry-run]      # refresh objective columns from OpenRouter; --dry-run diffs only
  models.py seed [--force]         # write the initial table (the 8 models we start from)
  models.py set-tier --key K --domain D --tier N [--note "..."]   # write an overlay correction

All output is JSON on stdout; diagnostics on stderr; exit 1 on error.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from _common import age_days, emit, fail, model_table_path, now_iso, ssl_context

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# The four token classes that get billed, and what they're called in the table.
# Cache reads DOMINATE real agentic sessions — a live PWC worker showed 32M
# cache-read tokens against 30k input — so pricing on in/out alone is wrong by
# orders of magnitude. All four are carried, all four are fetched.
PRICE_FIELDS = ("cost_in", "cost_out", "cache_read", "cache_write")

DOMAINS = ("code-review", "implementation", "research-writing", "ops-comms")

# Objective columns a refresh is allowed to overwrite. Anything not in here
# (notably `tiers`) is yours and is preserved across a fetch.
_FETCHED_FIELDS = (*PRICE_FIELDS, "context")

_STALE_DAYS = 7.0


# ── the seed table ────────────────────────────────────────────────────────────
# Costs/context are filled in by `fetch` from OpenRouter; the values here are a
# reasonable starting point so a fresh table is usable before its first refresh.
# TIERS ARE A HYPOTHESIS, not a measurement — deliberately conservative, and meant
# to be corrected by real outcomes (`set-tier`, written by /pwc-report-status at
# task close). Don't treat them as authoritative; treat them as a starting bet.
#
# `trusted` is a SEPARATE axis from capability, and conflating the two is a real
# bug we shipped and caught: "is this model good enough?" and "may this model see
# production data?" are different questions. A cheap third-party model can be
# perfectly capable and still be somewhere you don't want customer data going. So
# trust is declared per-row, by you, and the router filters on it independently of
# tiers. Default here: the harnesses whose providers you already have a commercial
# relationship with (Anthropic, OpenAI) are trusted; the metered OpenRouter
# passthroughs are not. Flip any of these in the overlay if you disagree.
_SEED = [
    # key, harness, dispatch model, catalog id, trusted, tiers
    ("claude/opus", "claude", "opus", "anthropic/claude-opus-4.8", True,
     {"code-review": 5, "implementation": 5, "research-writing": 5, "ops-comms": 4}),
    ("claude/fable", "claude", "fable", "anthropic/claude-fable-5", True,
     {"code-review": 5, "implementation": 5, "research-writing": 5, "ops-comms": 4}),
    ("claude/sonnet", "claude", "sonnet", "anthropic/claude-sonnet-5", True,
     {"code-review": 4, "implementation": 4, "research-writing": 4, "ops-comms": 4}),
    ("claude/haiku", "claude", "haiku", "anthropic/claude-haiku-4.5", True,
     {"code-review": 2, "implementation": 2, "research-writing": 2, "ops-comms": 3}),
    ("codex/gpt-5.5", "codex", "gpt-5.5", "openai/gpt-5.5", True,
     {"code-review": 4, "implementation": 5, "research-writing": 4, "ops-comms": 3}),
    ("opencode/glm-5.2", "opencode", "openrouter/z-ai/glm-5.2", "z-ai/glm-5.2", False,
     {"code-review": 3, "implementation": 4, "research-writing": 3, "ops-comms": 3}),
    ("opencode/kimi-k2.6", "opencode", "openrouter/moonshotai/kimi-k2.6",
     "moonshotai/kimi-k2.6", False,
     {"code-review": 3, "implementation": 3, "research-writing": 4, "ops-comms": 3}),
    ("opencode/deepseek-v4-pro", "opencode", "openrouter/deepseek/deepseek-v4-pro",
     "deepseek/deepseek-v4-pro", False,
     {"code-review": 3, "implementation": 3, "research-writing": 3, "ops-comms": 3}),
]


def _seed_table() -> dict:
    return {
        "version": 1,
        "fetched_at": None,  # never fetched -> stale by definition
        "models": [
            {"key": key, "harness": harness, "model": model, "catalog_id": catalog,
             "context": None, "available": None, "trusted": trusted,
             **{f: None for f in PRICE_FIELDS},
             "tiers": dict(tiers)}
            for key, harness, model, catalog, trusted, tiers in _SEED
        ],
        "overlay": {},
    }


# ── load / save / merge ───────────────────────────────────────────────────────
def load_raw(*, must_exist: bool = True) -> dict:
    """The table exactly as stored — overlay NOT applied. For writers."""
    p = model_table_path()
    if not p.exists():
        if must_exist:
            fail(f"no model table at {p} — run `pwc models seed` to create it")
        return _seed_table()
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError) as e:
        fail(f"could not read model table at {p}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        fail(f"malformed model table at {p}: expected an object with a 'models' list")
    data.setdefault("overlay", {})
    return data


def save(data: dict) -> None:
    p = model_table_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def merged_models(data: dict) -> list[dict]:
    """Rows with the overlay applied — what every READER should use.

    The overlay is the user's own calibration (tier corrections + notes), kept in a
    separate object so `fetch` can rewrite the rows wholesale without touching it.
    Merging here (rather than storing corrections inline) is what guarantees a
    refresh can never silently revert a judgment the user made.
    """
    out = []
    for row in data.get("models", []):
        row = copy.deepcopy(row)
        ov = (data.get("overlay") or {}).get(row.get("key")) or {}
        if ov.get("tiers"):
            row.setdefault("tiers", {}).update(ov["tiers"])
        if ov.get("note"):
            row["note"] = ov["note"]
        if ov.get("available") is not None:  # a manual "don't route here" veto
            row["available"] = ov["available"]
        if ov.get("trusted") is not None:  # "I'm fine sending real data here" (or not)
            row["trusted"] = ov["trusted"]
        out.append(row)
    return out


def table(*, must_exist: bool = True) -> dict:
    """The merged table — the reader's entry point (route.py, cost.py)."""
    data = load_raw(must_exist=must_exist)
    return {**data, "models": merged_models(data)}


def price_of(row: dict) -> dict:
    """The four USD-per-Mtok prices for a row, with missing ones as 0.0."""
    return {f: float(row.get(f) or 0.0) for f in PRICE_FIELDS}


# ── availability: which harnesses can this machine actually dispatch? ──────────
def _opencode_authed() -> bool:
    """opencode routes to OpenRouter/etc only if a credential is configured.

    Without this check the router would happily pick a model that fails at spawn —
    and the design says a failed dispatch surfaces to the user rather than silently
    falling back, so it must never be ROUTED to in the first place.
    """
    try:
        out = subprocess.run(["opencode", "auth", "list"], capture_output=True,
                             text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "0 credentials" not in out


def harness_available(harness: str) -> bool:
    """Is this harness installed AND able to reach its provider?"""
    if shutil.which(harness) is None:
        return False
    if harness == "opencode":
        return _opencode_authed()
    return True  # claude/codex authenticate out-of-band (subscription login)


# ── fetch: refresh the objective columns ──────────────────────────────────────
def _openrouter_catalog() -> dict[str, dict]:
    """{catalog_id: {cost_in, cost_out, cache_read, cache_write, context}} from
    OpenRouter's free, unauthenticated catalog. Prices there are USD PER TOKEN as
    strings; the table stores USD per MILLION tokens, which is the unit humans
    reason in."""
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh) pwc-models/1"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        fail(f"could not fetch the OpenRouter catalog: {type(e).__name__}: {e}")

    def per_mtok(v):
        try:
            return round(float(v) * 1_000_000, 4)
        except (TypeError, ValueError):
            return None

    catalog = {}
    for m in payload.get("data", []):
        pricing = m.get("pricing") or {}
        catalog[m["id"]] = {
            "cost_in": per_mtok(pricing.get("prompt")),
            "cost_out": per_mtok(pricing.get("completion")),
            "cache_read": per_mtok(pricing.get("input_cache_read")),
            "cache_write": per_mtok(pricing.get("input_cache_write")),
            "context": m.get("context_length"),
        }
    return catalog


def compute_fetch(data: dict) -> tuple[dict, list[dict]]:
    """Return (new_table, changes). Pure-ish: does the network read, then diffs.

    `changes` is a list of {key, field, old, new} — what /pwc-find-work PROPOSES to
    the user. Nothing is written here; the caller decides.
    """
    catalog = _openrouter_catalog()
    new = copy.deepcopy(data)
    changes = []
    for row in new["models"]:
        key = row.get("key")

        # objective columns from the catalog
        entry = catalog.get(row.get("catalog_id"))
        if entry is None:
            if row.get("catalog_id"):
                changes.append({"key": key, "field": "catalog_id", "old": row["catalog_id"],
                                "new": None, "note": "not found in the OpenRouter catalog"})
        else:
            for field in _FETCHED_FIELDS:
                old, fresh = row.get(field), entry.get(field)
                if fresh is not None and old != fresh:
                    changes.append({"key": key, "field": field, "old": old, "new": fresh})
                    row[field] = fresh

        # availability is probed locally, not fetched
        avail = harness_available(row.get("harness", ""))
        if row.get("available") != avail:
            changes.append({"key": key, "field": "available",
                            "old": row.get("available"), "new": avail})
            row["available"] = avail

    new["fetched_at"] = now_iso()
    return new, changes


# ── commands ──────────────────────────────────────────────────────────────────
def cmd_show(args):
    data = table()
    models = data["models"]
    if args.available:
        models = [m for m in models if m.get("available")]
    emit({"fetched_at": data.get("fetched_at"),
          "age_days": age_days(data.get("fetched_at") or ""),
          "models": models,
          "overlay": data.get("overlay", {})})


def cmd_stale(args):
    """The refresh clock /pwc-find-work checks at step 0.

    A never-fetched table (fetched_at: null) is stale by definition — its costs are
    the seed's guesses, not reality.
    """
    p = model_table_path()
    if not p.exists():
        emit({"stale": True, "exists": False, "age_days": None,
              "threshold_days": args.days,
              "why": f"no model table at {p} — run `pwc models seed`"})
        return
    data = load_raw()
    fetched = data.get("fetched_at")
    age = age_days(fetched or "")
    stale = age is None or age > args.days
    emit({"stale": stale, "exists": True, "fetched_at": fetched, "age_days": age,
          "threshold_days": args.days,
          "why": ("never fetched — costs are seed guesses" if age is None
                  else f"last refreshed {age:.1f}d ago"
                       f"{' (over threshold)' if stale else ''}")})


def cmd_fetch(args):
    """Refresh the objective columns. --dry-run diffs WITHOUT writing.

    /pwc-find-work runs `--dry-run` first and shows the changes as proposals; it
    writes only after the user confirms — the same "surface, never auto-apply" rule
    the skill uses for task candidates.
    """
    data = load_raw(must_exist=False)
    new, changes = compute_fetch(data)
    if not args.dry_run:
        save(new)
    emit({"written": not args.dry_run, "fetched_at": new["fetched_at"],
          "changes": changes, "change_count": len(changes)})


def cmd_seed(args):
    p = model_table_path()
    if p.exists() and not args.force:
        fail(f"model table already exists at {p} — pass --force to overwrite "
             f"(this DISCARDS your overlay/tier calibration)")
    data = _seed_table()
    save(data)
    emit({"written": str(p), "models": [m["key"] for m in data["models"]],
          "note": "objective columns are empty — run `pwc models fetch` to fill them"})


def cmd_set_tier(args):
    """Write a capability correction into the overlay (never into the row).

    This is the feedback path: /pwc-report-status calls it at task close when the
    user says a model was overkill or too weak for a domain. Because it lands in the
    overlay, the next `fetch` cannot revert it.
    """
    if args.domain not in DOMAINS:
        fail(f"unknown domain {args.domain!r} — known: {', '.join(DOMAINS)}")
    if not 1 <= args.tier <= 5:
        fail(f"tier must be 1-5 (got {args.tier})")
    data = load_raw()
    if not any(m.get("key") == args.key for m in data["models"]):
        known = ", ".join(m.get("key", "?") for m in data["models"])
        fail(f"unknown model key {args.key!r} — known: {known}")
    ov = data.setdefault("overlay", {}).setdefault(args.key, {})
    tiers = ov.setdefault("tiers", {})
    old = tiers.get(args.domain)
    tiers[args.domain] = args.tier
    if args.note:
        ov["note"] = args.note
    ov["updated_at"] = now_iso()
    save(data)
    emit({"key": args.key, "domain": args.domain, "old": old, "new": args.tier,
          "note": ov.get("note"), "overlay": ov})


def main(argv=None):
    p = argparse.ArgumentParser(prog="models.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="the merged table (overlay applied)")
    s.add_argument("--available", action="store_true",
                   help="only models this machine can actually dispatch")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("stale", help="is the table due a refresh?")
    s.add_argument("--days", type=float, default=_STALE_DAYS)
    s.set_defaults(func=cmd_stale)

    s = sub.add_parser("fetch", help="refresh objective columns from OpenRouter")
    s.add_argument("--dry-run", action="store_true",
                   help="report the changes WITHOUT writing (for confirmation)")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("seed", help="write the initial model table")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("set-tier", help="record a capability correction (overlay)")
    s.add_argument("--key", required=True)
    s.add_argument("--domain", required=True, choices=DOMAINS)
    s.add_argument("--tier", required=True, type=int)
    s.add_argument("--note")
    s.set_defaults(func=cmd_set_tier)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except Exception as e:  # noqa: BLE001
        fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
