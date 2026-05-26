#!/usr/bin/env python3
"""Per-workspace sources config — what /find-work scans and how.

Different workspaces draw work from different places (one is Jira + GitHub + Slack,
another is just local notes), so the set of sources and their query parameters live
per-workspace in <workspace>/.pwc/sources.json — not hardcoded in the skill. This
script is the single read/write/validate path to that file; /find-work reads it,
/setup-workspace writes it.

Config shape (JSON):
  {
    "sources": {
      "jira":   {"enabled": true,  "project": "SMT",
                 "jql": "assignee = currentUser() AND statusCategory != Done",
                 "default_type": "jira"},
      "github": {"enabled": true,  "org": "taxit-tech",
                 "watch": ["review-requested", "assigned"]},
      "slack":  {"enabled": true,  "channels": ["#eng", "#support"]},
      "email":  {"enabled": false}
    }
  }

Usage:
  sources.py show                 # print the config as JSON (init-empty if absent)
  sources.py set --json -         # replace the whole config from JSON on stdin
  sources.py enabled              # print only the enabled sources (what /find-work scans)

All output is JSON on stdout; diagnostics on stderr; exit 1 on error.
"""

from __future__ import annotations

import argparse
import json
import sys

from _common import config_path, emit, fail, read_json_stdin

# Known source kinds and which fields each expects. Validation is advisory — unknown
# keys are allowed (forward-compatible), but a known source missing required fields
# is flagged so /setup-workspace produces something /find-work can actually use.
_KNOWN = {
    "jira": ("project", "jql"),
    "github": ("org",),
    "slack": ("channels",),
    "email": (),
}

_EMPTY = {"sources": {}}


def _load(workspace):
    p = config_path(workspace)
    if not p.exists():
        return dict(_EMPTY)
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError) as e:
        fail(f"could not read sources config at {p}: {e}")
    if not isinstance(data, dict) or "sources" not in data:
        fail(f"malformed sources config at {p}: expected an object with a 'sources' key")
    return data


def _validate(data) -> list[str]:
    """Return a list of advisory warnings (missing fields on enabled known sources)."""
    warnings = []
    for name, cfg in data.get("sources", {}).items():
        if not isinstance(cfg, dict):
            warnings.append(f"source {name!r} is not an object")
            continue
        if not cfg.get("enabled"):
            continue
        for field in _KNOWN.get(name, ()):
            if not cfg.get(field):
                warnings.append(f"enabled source {name!r} is missing '{field}'")
    return warnings


def cmd_show(args):
    data = _load(args.workspace)
    warnings = _validate(data)
    if warnings:
        for w in warnings:
            print(f"pwc: warning: {w}", file=sys.stderr)
    emit(data)


def cmd_enabled(args):
    """Just the enabled sources — the slice /find-work actually iterates."""
    data = _load(args.workspace)
    enabled = {n: c for n, c in data.get("sources", {}).items()
               if isinstance(c, dict) and c.get("enabled")}
    emit({"sources": enabled})


def cmd_set(args):
    """Replace the whole config from a JSON body on stdin (--json -)."""
    if args.json != "-":
        fail("set: only --json - (read from stdin) is supported")
    data = read_json_stdin()
    if not isinstance(data, dict) or "sources" not in data:
        fail("set: stdin must be a JSON object with a 'sources' key")
    p = config_path(args.workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    warnings = _validate(data)
    for w in warnings:
        print(f"pwc: warning: {w}", file=sys.stderr)
    emit({"written": str(p), "sources": list(data["sources"].keys())})


def main(argv=None):
    p = argparse.ArgumentParser(prog="sources.py", description=__doc__)
    p.add_argument("--workspace", help="workspace root (default: discover from cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").set_defaults(func=cmd_show)
    sub.add_parser("enabled").set_defaults(func=cmd_enabled)
    s = sub.add_parser("set")
    s.add_argument("--json", metavar="-", required=True)
    s.set_defaults(func=cmd_set)
    args = p.parse_args(argv)
    try:
        args.func(args)
    except Exception as e:  # noqa: BLE001
        fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
