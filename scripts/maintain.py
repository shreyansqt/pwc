#!/usr/bin/env python3
"""`pwc maintain` — keep the harnesses and the model table current, once a day.

Nobody has to remember to run this: `pwc spawn` calls `run_if_due()` before the
first spawn of each local day. It does three jobs:

  1. UPDATE HARNESSES — run each harness's own update command. A harness with a
     live process is SKIPPED: replacing the binary under a running worker can break
     it. Claude Code is not listed because it updates itself (and the coordinator
     is itself a live claude process).

  2. DISCOVER MODELS — ask each harness which models it serves now:
     - a model the table does not know gets a row with `needs_tiers: true` and no
       tiers. Route rejects a model with no tier, so a new model never takes work
       before Shreyans sets its tiers (`pwc models set-tier`). /pwc-show-work lists
       these rows so the gap is visible.
     - a row whose model the harness no longer lists is set `available: false`.
     Harnesses that cannot list models (claude: aliases only) are left alone.

  3. REFRESH PRICES — the existing OpenRouter price refresh (`models fetch`).

A failure in one step is reported and the next step still runs. Maintenance never
blocks a spawn: `run_if_due` catches everything and prints a warning.

Usage:
  maintain.py run [--force] [--dry-run]   # --force ignores the once-a-day marker
  maintain.py status                      # when it last ran, and what it did
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import models as models_mod
from _common import emit, now_iso

# harness -> its own update command. Not claude or cursor-agent: they auto-update
# (cursor-agent unpacks a new version in the background at launch, seen 2026-10-21).
UPDATE_COMMANDS = {
    "codex": ["codex", "update"],
    "grok": ["grok", "update"],
    "agy": ["agy", "update"],
}

# Harnesses whose model list is too long to add every model as a row: cursor-agent
# lists 264 (2026-10-21), one per model x effort x speed. Their rows are added by
# hand; discovery only retires a row whose model is gone, or restores it.
NO_NEW_ROWS = ("cursor-agent",)

_TIMEOUT = 300


def _marker_path() -> Path:
    base = os.environ.get("PWC_CONFIG_DIR") or str(Path.home() / ".config" / "pwc")
    return Path(base) / "maintain-last.json"


def _load_marker() -> dict:
    import json
    try:
        return json.loads(_marker_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_marker(data: dict) -> None:
    import json
    p = _marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def is_due(today: date | None = None) -> bool:
    return _load_marker().get("date") != (today or date.today()).isoformat()


# ── 1. harness updates ────────────────────────────────────────────────────────
def _running(harness: str) -> bool:
    """Is any process of this harness alive? Checks the command name and the
    resolved binary name (grok's link points at `grok-macos-aarch64`)."""
    path = shutil.which(harness)
    names = {harness}
    if path:
        names |= {Path(path).name, Path(os.path.realpath(path)).name}
    for name in names:
        r = subprocess.run(["pgrep", "-x", name], capture_output=True)
        if r.returncode == 0:
            return True
    return False


def update_harnesses(harnesses: list[str], *, dry_run: bool = False) -> list[dict]:
    out = []
    for h in harnesses:
        cmd = UPDATE_COMMANDS.get(h)
        if cmd is None:
            continue
        if shutil.which(cmd[0]) is None:
            out.append({"harness": h, "result": "not installed"})
            continue
        if _running(h):
            out.append({"harness": h, "result": "skipped: a process is running"})
            continue
        if dry_run:
            out.append({"harness": h, "result": f"would run: {' '.join(cmd)}"})
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT,
                               stdin=subprocess.DEVNULL)
            tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
            out.append({"harness": h, "result": "ok" if r.returncode == 0 else
                        f"exit {r.returncode}", "detail": tail[0][:200]})
        except (OSError, subprocess.SubprocessError) as e:
            out.append({"harness": h, "result": f"failed: {type(e).__name__}: {e}"})
    return out


# ── 2. model discovery ────────────────────────────────────────────────────────
def _lines(cmd: list[str]) -> list[str] | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def parse_grok_models(lines: list[str]) -> set[str]:
    """`grok models` prints `  * grok-4.7 (default)` / `  - grok-4.6`."""
    return {m.group(1) for m in (re.match(r"\s*[*-]\s+(\S+)", ln) for ln in lines)
            if m}


def parse_cursor_models(lines: list[str]) -> set[str]:
    """`cursor-agent models` prints `<id> - <display name>` under a heading. `auto`
    is left out: it lets Cursor pick the model, so route cannot know what runs."""
    return {m.group(1) for m in (re.match(r"(\S+) - \S", ln) for ln in lines)
            if m and m.group(1) != "auto"}


def parse_agy_models(lines: list[str]) -> set[str]:
    """`agy models` prints `<id>\\t<display name>` after a `Fetching…` line."""
    return {ln.split("\t")[0].strip() for ln in lines
            if "\t" in ln and ln.split("\t")[0].strip()}


def list_models(harness: str) -> set[str] | None:
    """What the harness serves right now, or None if it cannot say."""
    if harness == "codex":
        return models_mod._codex_model_slugs()
    if harness == "grok":
        lines = _lines(["grok", "models"])
        return parse_grok_models(lines) if lines is not None else None
    if harness == "cursor-agent":
        lines = _lines(["cursor-agent", "models"])
        return parse_cursor_models(lines) if lines is not None else None
    if harness == "agy":
        lines = _lines(["agy", "models"])
        return parse_agy_models(lines) if lines is not None else None
    return None


def reconcile(data: dict, served: dict[str, set[str]]) -> list[dict]:
    """Add rows for new models, retire rows for gone ones. Mutates `data`. Pure
    apart from that: `served` is {harness: models it lists}. A harness in
    NO_NEW_ROWS only gets its existing rows retired or restored."""
    changes = []
    rows = data.setdefault("models", [])
    for harness, names in served.items():
        known = {r.get("model") for r in rows if r.get("harness") == harness}
        new = set() if harness in NO_NEW_ROWS else names - known
        for name in sorted(new):
            rows.append({"key": f"{harness}/{name}", "harness": harness,
                         "model": name, "catalog_id": None, "context": None,
                         "available": True, "data_ok": False, "needs_tiers": True,
                         **{f: None for f in models_mod.PRICE_FIELDS},
                         "tiers": {}, "discovered_at": now_iso()})
            changes.append({"key": f"{harness}/{name}", "change": "new model",
                            "action": "set its tiers with `pwc models set-tier`"})
        for row in rows:
            if row.get("harness") != harness:
                continue
            listed = row.get("model") in names
            if not listed and not row.get("retired"):
                row["retired"] = True
                row["available"] = False
                changes.append({"key": row.get("key"), "change": "gone from the "
                                f"{harness} model list", "action": "retired"})
            elif listed and row.get("retired"):
                row.pop("retired")
                row["available"] = True
                changes.append({"key": row.get("key"), "change": "listed again",
                                "action": "un-retired"})
    return changes


# ── run ───────────────────────────────────────────────────────────────────────
def run(*, dry_run: bool = False) -> dict:
    data = models_mod.load_raw(must_exist=False)
    harnesses = sorted({r.get("harness") for r in data.get("models", [])
                        if r.get("harness")} | set(UPDATE_COMMANDS))
    report = {"at": now_iso(), "updates": update_harnesses(harnesses, dry_run=dry_run)}

    # Prices first: `compute_fetch` also re-derives `available` from the installed
    # binary, which would re-enable a model that discovery retires below.
    try:
        data, price_changes = models_mod.compute_fetch(data)
        report["price_changes"] = len(price_changes)
    except SystemExit:
        report["price_changes"] = "refresh failed (offline?)"

    served = {}
    for h in harnesses:
        names = list_models(h)
        if names:  # None = cannot say; empty = probably logged out, so do not retire
            served[h] = names
    report["models"] = reconcile(data, served)

    if not dry_run:
        models_mod.save(data)
        _save_marker({"date": date.today().isoformat(), **report})
    report["needs_tiers"] = [r["key"] for r in models_mod.merged_models(data)
                             if r.get("needs_tiers") and not r.get("tiers")]
    return report


def run_if_due() -> dict | None:
    """Called by `pwc spawn`. Never raises: maintenance must not block a spawn."""
    if not is_due():
        return None
    print("pwc: daily maintenance (harness updates, model list) …", file=sys.stderr)
    try:
        report = run()
    except BaseException as e:  # noqa: BLE001 — includes fail()'s SystemExit
        print(f"pwc: WARNING — maintenance failed ({type(e).__name__}: {e}); "
              f"spawning anyway", file=sys.stderr)
        return None
    for u in report["updates"]:
        print(f"pwc:   {u['harness']}: {u['result']}", file=sys.stderr)
    for c in report["models"]:
        print(f"pwc:   {c['key']}: {c['change']}", file=sys.stderr)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(prog="maintain.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="update harnesses and the model table now")
    s.add_argument("--force", action="store_true",
                   help="run even if maintenance already ran today")
    s.add_argument("--dry-run", action="store_true",
                   help="report what would change, write nothing")
    s = sub.add_parser("status", help="when maintenance last ran, and what it did")
    args = p.parse_args(argv)
    if args.cmd == "status":
        emit({"due": is_due(), "last": _load_marker() or None})
        return
    if not (args.force or args.dry_run) and not is_due():
        emit({"skipped": "already ran today", "last": _load_marker()})
        return
    emit(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main(sys.argv[1:])
