#!/usr/bin/env python3
"""`pwc quota` — how much of each subscription is left, read from CodexBar.

Routing used to be cost-only, so it could not see that a plan was nearly empty or
that a cheaper plan sat unused. This module gives `pwc route` one number per
SUBSCRIPTION (not per model): how far that plan's quota is behind or ahead of an
even pace through its current window.

Why CodexBar: it already reads every provider's real usage (Claude, Codex, Grok,
and more) through one CLI with one JSON shape. PWC calls it instead of parsing each
harness's logs. Path: /Applications/CodexBar.app/Contents/Helpers/CodexBarCLI, or
$PWC_CODEXBAR.

A subscription can serve several harnesses (the X plan serves Grok Build and the
Cursor CLI from one pool), so route compares subscriptions, then picks a model
inside the winner. The mapping lives in DEFAULT_SUBSCRIPTIONS and can be overridden
by a top-level `subscriptions` object in the model table.

Status per subscription:
  ok        — CodexBar returned usage; `slack` and `windows` are set.
  exhausted — a window is >= EXHAUSTED_PCT used, or projected to run out before it
              resets, or a manual mark (`pwc quota mark-empty`) is still active.
  unknown   — CodexBar could not read it (not logged in, provider unsupported,
              CodexBar missing). Route gives an unknown plan only a small share.

`slack` = expected-used% minus used% for the LONGEST window (the weekly one).
Positive means the plan is behind pace: quota that will be wasted at reset unless
work goes there. Route sends work to the plan with the largest slack.

Usage:
  quota.py show [--fresh]                       # all subscriptions, JSON
  quota.py mark-empty --subscription S [--hours 24] --why "..."
  quota.py clear-mark --subscription S
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _common import emit, fail, now_iso, parse_iso

CODEXBAR_DEFAULT = "/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI"

# One entry per paid plan. `codexbar` is the argument list after `usage`; None means
# CodexBar cannot read this plan (see the note), so it is always `unknown`.
DEFAULT_SUBSCRIPTIONS = {
    "claude": {
        "plan": "Claude Max 5x",
        "harnesses": ["claude"],
        "codexbar": ["--provider", "claude", "--source", "cli"],
    },
    "chatgpt": {
        "plan": "ChatGPT Pro Lite",
        "harnesses": ["codex"],
        "codexbar": ["--provider", "codex"],
    },
    "x": {
        "plan": "X Plus (SuperGrok Lite)",
        "harnesses": ["grok", "cursor-agent"],
        "codexbar": ["--provider", "grok"],
    },
    "google-ai": {
        "plan": "Google AI Plus",
        "harnesses": ["agy"],
        # CodexBar reads Antigravity only through the desktop app's language
        # server. The CLI does not start one (checked 2026-10-13), and Shreyans
        # does not want the desktop app, so this plan stays `unknown`.
        "codexbar": None,
    },
}

# A window at or above this share is treated as empty: a worker started now would
# likely stop half way.
EXHAUSTED_PCT = 90.0
# Only project a window's end-of-window use once this share of it has passed; earlier
# projections swing wildly on one busy hour.
_PROJECT_MIN_ELAPSED = 0.25
# A window without a length from CodexBar is assumed weekly (the X pool resets weekly).
_DEFAULT_WINDOW_MIN = 7 * 24 * 60

_CACHE_SECONDS = 300
_CODEXBAR_TIMEOUT = 60


def _config_dir() -> Path:
    return Path(os.environ.get("PWC_CONFIG_DIR", Path.home() / ".config" / "pwc"))


def _cache_path() -> Path:
    return _config_dir() / "quota-cache.json"


def _marks_path() -> Path:
    return _config_dir() / "quota-marks.json"


def codexbar_path() -> str | None:
    p = os.environ.get("PWC_CODEXBAR") or CODEXBAR_DEFAULT
    return p if Path(p).exists() else None


def subscriptions(table: dict | None = None) -> dict:
    """The subscription map: the defaults, overridden per key by the table."""
    subs = {k: dict(v) for k, v in DEFAULT_SUBSCRIPTIONS.items()}
    for key, override in ((table or {}).get("subscriptions") or {}).items():
        subs[key] = {**subs.get(key, {}), **override}
    return subs


def subscription_for_harness(harness: str, subs: dict) -> str | None:
    for key, sub in subs.items():
        if harness in (sub.get("harnesses") or []):
            return key
    return None


# ── pure: turn CodexBar's JSON into a status ──────────────────────────────────
def _window(raw: dict | None, now: datetime) -> dict | None:
    if not raw or raw.get("usedPercent") is None:
        return None
    minutes = raw.get("windowMinutes") or _DEFAULT_WINDOW_MIN
    used = float(raw["usedPercent"])
    resets = parse_iso(raw.get("resetsAt") or "")
    elapsed = None
    if resets is not None:
        left = (resets - now).total_seconds() / 60
        elapsed = min(1.0, max(0.0, 1 - left / minutes))
    w = {"used_pct": used, "window_minutes": minutes,
         "resets_at": raw.get("resetsAt"), "elapsed": elapsed}
    if elapsed is not None:
        w["expected_pct"] = round(elapsed * 100, 1)
        if elapsed >= _PROJECT_MIN_ELAPSED:
            w["projected_pct"] = round(used / elapsed, 1)
    return w


def status_from_codexbar(entry: dict, *, now: datetime) -> dict:
    """One CodexBar provider entry -> {status, windows, slack, why}. No I/O."""
    if entry.get("error"):
        return {"status": "unknown",
                "why": f"CodexBar: {entry['error'].get('message', 'error')}"[:300]}
    usage = entry.get("usage") or {}
    windows = [w for w in (_window(usage.get(k), now)
                           for k in ("primary", "secondary", "tertiary")) if w]
    if not windows:
        return {"status": "unknown", "why": "CodexBar returned no usage windows"}
    out = {"windows": windows, "login": usage.get("loginMethod"),
           "confidence": usage.get("dataConfidence")}
    longest = max(windows, key=lambda w: w["window_minutes"])
    if longest.get("expected_pct") is not None:
        out["slack"] = round(longest["expected_pct"] - longest["used_pct"], 1)
    for w in windows:
        span = "weekly" if w["window_minutes"] >= 7 * 24 * 60 else \
            f"{w['window_minutes'] // 60}h"
        if w["used_pct"] >= EXHAUSTED_PCT:
            return {**out, "status": "exhausted",
                    "why": f"{span} window {w['used_pct']:g}% used "
                           f"(resets {w['resets_at']})"}
        if w.get("projected_pct", 0) >= 100:
            return {**out, "status": "exhausted",
                    "why": f"{span} window {w['used_pct']:g}% used and on pace for "
                           f"{w['projected_pct']:g}% before it resets "
                           f"({w['resets_at']})"}
    return {**out, "status": "ok",
            "why": f"weekly slack {out.get('slack', 0):+g} points"}


def apply_marks(status: dict, marks: dict, *, now: datetime) -> dict:
    """Overlay manual `mark-empty` entries that have not expired yet. No I/O."""
    out = {}
    for key, st in status.items():
        mark = marks.get(key)
        until = parse_iso((mark or {}).get("until") or "")
        if until is not None and until > now:
            st = {**st, "status": "exhausted",
                  "why": f"marked empty until {mark['until']}: {mark.get('why', '')}"}
        out[key] = st
    return out


# ── I/O ───────────────────────────────────────────────────────────────────────
def _run_codexbar(args: list[str]) -> dict:
    cli = codexbar_path()
    if cli is None:
        return {"error": {"message": "CodexBar CLI not found "
                                     f"(looked at {CODEXBAR_DEFAULT} and $PWC_CODEXBAR)"}}
    try:
        out = subprocess.run([cli, "usage", *args, "--format", "json"],
                             capture_output=True, text=True,
                             timeout=_CODEXBAR_TIMEOUT)
        data = json.loads(out.stdout or "null")
    except subprocess.TimeoutExpired:
        return {"error": {"message": f"CodexBar timed out after {_CODEXBAR_TIMEOUT}s"}}
    except (OSError, ValueError) as e:
        return {"error": {"message": f"CodexBar failed: {type(e).__name__}: {e}"}}
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return {"error": {"message": "CodexBar returned no JSON object"}}
    return data


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _save_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def read(table: dict | None = None, *, fresh: bool = False,
         now: datetime | None = None) -> dict:
    """{subscription: status} for every subscription, with marks applied.

    Cached for a few minutes: CodexBar takes seconds per provider, and a burst of
    spawns should not hit every provider's API each time. `fresh` skips the cache.
    """
    now = now or datetime.now(timezone.utc)
    subs = subscriptions(table)
    cache = {} if fresh else _load_json(_cache_path())
    cached_at = parse_iso(cache.get("fetched_at") or "")
    raw = cache.get("raw") or {}
    if cached_at is None or (now - cached_at).total_seconds() > _CACHE_SECONDS \
            or set(raw) != set(subs):
        raw = {key: (_run_codexbar(sub["codexbar"]) if sub.get("codexbar") else
                     {"error": {"message": "CodexBar cannot read this plan "
                                           "(no CLI source)"}})
               for key, sub in subs.items()}
        _save_json(_cache_path(), {"fetched_at": now_iso(), "raw": raw})
    status = {key: {**status_from_codexbar(raw.get(key) or {}, now=now),
                    "plan": subs[key].get("plan"),
                    "harnesses": subs[key].get("harnesses", [])}
              for key in subs}
    return apply_marks(status, _load_json(_marks_path()), now=now)


# ── commands ──────────────────────────────────────────────────────────────────
def cmd_show(args):
    import models as models_mod
    table = models_mod.load_raw(must_exist=False)
    emit({"fetched_at": now_iso(), "subscriptions": read(table, fresh=args.fresh)})


def cmd_mark_empty(args):
    """Record that a plan ran out, for plans CodexBar cannot read.

    A worker or the coordinator calls this when a run stops with a quota error.
    Route then skips the plan until the mark expires. It never moves the task to
    another harness by itself: that stays the user's decision.
    """
    subs = subscriptions()
    if args.subscription not in subs:
        fail(f"unknown subscription {args.subscription!r} — known: {', '.join(subs)}")
    until = (datetime.now(timezone.utc) + timedelta(hours=args.hours)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    marks = _load_json(_marks_path())
    marks[args.subscription] = {"until": until, "why": args.why, "at": now_iso()}
    _save_json(_marks_path(), marks)
    emit({"subscription": args.subscription, "mark": marks[args.subscription]})


def cmd_clear_mark(args):
    marks = _load_json(_marks_path())
    old = marks.pop(args.subscription, None)
    _save_json(_marks_path(), marks)
    emit({"subscription": args.subscription, "cleared": old})


def main(argv=None):
    p = argparse.ArgumentParser(prog="quota.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="quota status of every subscription")
    s.add_argument("--fresh", action="store_true", help="skip the 5-minute cache")
    s.set_defaults(func=cmd_show)
    s = sub.add_parser("mark-empty", help="mark a plan as out of quota for a while")
    s.add_argument("--subscription", required=True)
    s.add_argument("--hours", type=float, default=24.0)
    s.add_argument("--why", required=True)
    s.set_defaults(func=cmd_mark_empty)
    s = sub.add_parser("clear-mark", help="remove a mark-empty entry")
    s.add_argument("--subscription", required=True)
    s.set_defaults(func=cmd_clear_mark)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
