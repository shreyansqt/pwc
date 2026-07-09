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
                 "id_convention": "jira-key"},
      "github": {"enabled": true,  "org": "taxit-tech",
                 "watch": ["review-requested", "assigned"],
                 "id_convention": "github-slug"},
      "slack":  {"enabled": true,  "channels": ["#eng", "#support"],
                 "id_convention": "slack-slug"},
      "email":  {"enabled": false, "id_convention": "email-slug"}
    },
    "id_fallback": "task-slug",
    "skill_hints": {
      "pr-review": ["code-review", "request-review"],
      "jira":      ["start-ticket"],
      "slack":     ["slack-message"],
      "investigation": ["db-query", "service-cli"]
    }
  }

"skill_hints" maps a task TYPE (or a free-form signal label) to the skill(s) that
help with it. /start-work looks up the task's type here and SUGGESTS the matching
skill in the worker's seed (as available, never commanded). Configured once at
/setup-workspace by scanning the available skills; reused on every spawn so the right
skill is offered without the user imposing it later. Unknown/extra keys are allowed.

Task ids are meaningful and derived per-source at creation. "id_convention" tells
/find-work how to build a new task's id from a given source:
  - "jira-key"   : use the Jira key itself (e.g. SMT-874).
  - "<p>-slug"   : a prefix plus a slug of the title (e.g. slack-deploy-window).
"id_fallback" is the convention for multi-source or sourceless tasks (default a
plain slug). taskdb.py dedups whatever id is produced; a task that later gains a
Jira key can be `promote`d to it (old id kept as an alias).

Usage:
  sources.py show                 # print the config as JSON (init-empty if absent)
  sources.py set --json -         # replace the whole config from JSON on stdin
  sources.py enabled              # print only the enabled sources (what /find-work scans)
  sources.py skill-hints [--type T]  # the task-type -> skill(s) map (or one type's list)
  sources.py priority             # the workspace's priority model (P1/P2/P3 rules), or {}
  sources.py routing              # the model/harness routing policy, or {}

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


def cmd_skill_hints(args):
    """The task-type -> skill(s) map (empty object if none configured).

    /start-work reads this to suggest the relevant skill in a worker's seed. If
    `--type` is given, return just that type's hints (a list, possibly empty).
    """
    data = _load(args.workspace)
    hints = data.get("skill_hints", {}) or {}
    if args.type:
        emit(hints.get(args.type, []))
    else:
        emit(hints)


def cmd_priority(args):
    """The workspace's priority model (empty object if none configured).

    /pwc-find-work reads this to set `--priority` on queued tasks, and /pwc-pick-work
    and /pwc-show-work read it to rank/label. Priority rules are workspace policy (they
    depend on the workspace's Jira columns, team conventions, single- vs multi-user,
    etc.), so they live here — NOT hardcoded in the generic skills. Shape:

        "priority": {
          "model": "<free-text prose: how P1/P2/P3 are decided here>",
          "tiers": {"1": "...", "2": "...", "3": "..."}   // optional one-line summaries
        }

    Returns {} when unset — the skills then fall back to their built-in generic default
    ("1 = someone's blocked on you, 2 = active work, 3 = solo/research").
    """
    data = _load(args.workspace)
    emit(data.get("priority", {}) or {})


def cmd_routing(args):
    """The workspace's model/harness routing policy (empty object if unset).

    /pwc-find-work reads this when queueing a task to set its `harness` and `model`
    (user-overridable at confirmation); /pwc-start-work dispatches with whatever the
    task carries. Routing is workspace policy — which models/subscriptions are
    available and what kind of work each is trusted with — so it lives here, NOT
    hardcoded in the skills. Shape:

        "routing": {
          "default": {"harness": "claude", "model": null},   // null model = harness default
          "rules": [                                          // first match wins
            {"match": {"type": "slack"}, "harness": "claude", "model": "haiku",
             "why": "quick replies don't need a big model"}
          ],
          "notes": "<free-text guidance for judgment calls the rules don't cover>"
        }

    The rules are guidance the coordinator applies with judgment (like the priority
    model), not a mechanical matcher. Returns {} when unset — the skills then default
    everything to the claude harness with its default model.
    """
    data = _load(args.workspace)
    emit(data.get("routing", {}) or {})


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
    s = sub.add_parser("skill-hints")
    s.add_argument("--type", help="return only this task type's hints (a list)")
    s.set_defaults(func=cmd_skill_hints)
    sub.add_parser("priority").set_defaults(func=cmd_priority)
    sub.add_parser("routing").set_defaults(func=cmd_routing)
    args = p.parse_args(argv)
    try:
        args.func(args)
    except Exception as e:  # noqa: BLE001
        fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
