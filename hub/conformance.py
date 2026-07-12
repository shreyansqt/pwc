#!/usr/bin/env python3
"""Conformance test: the hub backend must be indistinguishable from local.

Runs an identical operation sequence through the `pwc` CLI against two
workspaces — one local (sqlite), one hub-backed (store.json -> a deployed hub)
— then diffs the normalized outputs. Timestamps and the hub URL are the only
legitimate differences; anything else is a semantics drift in the Worker port.

Usage:
  hub/conformance.py --hub-url https://pwc-hub.<acct>.workers.dev \
                     [--hub-workspace conformance-test] [--token-file PATH]

Requires: `pwc` on PATH; the hub deployed; the hub workspace EMPTY (the test
refuses to run against a hub workspace that already has tasks).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# One sequence, exercising every op with logic in it. (op, args) pairs; args
# use the CLI flags. Ops whose output contains machine-variant noise get
# normalized before diffing.
SEQUENCE: list[list[str]] = [
    ["add-task", "--task", "CONF-1", "--type", "jira", "--title", "First conformance task",
     "--priority", "1", "--harness", "claude", "--workdir", "repo-a"],
    ["add-task", "--type", "slack", "--title", "Reply to conformance thread",
     "--priority", "2", "--harness", "opencode", "--model", "openrouter/z-ai/glm-5.2"],
    ["add-task", "--task", "CONF-1", "--type", "jira", "--title", "Dup id must dedup"],
    ["add-ref", "--task", "CONF-1", "--kind", "identity", "--ref-type", "jira_key",
     "--value", "CONF-1"],
    ["add-ref", "--task", "CONF-1", "--kind", "working", "--ref-type", "pr",
     "--value", "owner/repo#1", "--label", "the PR"],
    ["find-refs", "--ref-type", "jira_key", "--value", "CONF-1"],
    ["log-event", "--task", "CONF-1", "--kind", "note", "--detail", "a coordinator note"],
    ["log-event", "--task", "CONF-1", "--source", "worker", "--kind", "status",
     "--detail", "blocked on X", "--set-status", "blocked"],
    ["update-task", "--task", "CONF-1", "--status", "in-progress", "--priority", "2"],
    ["set-session", "--task", "CONF-1",
     "--session-id", "00000000-conf-4mce-0000-000000000001", "--workdir", "repo-a"],
    ["find-session", "--session-id", "00000000-conf-4mce-0000-000000000001"],
    ["clear-session", "--task", "CONF-1"],
    ["promote", "--task", "reply-to-conformance-thread", "--new-id", "CONF-9"],
    ["detail", "--task", "reply-to-conformance-thread"],  # via alias
    ["add-task", "--task", "CONF-ABSORB", "--type", "jira", "--title", "Will be merged"],
    ["merge", "--from", "CONF-ABSORB", "--into", "CONF-1"],
    ["archive", "--task", "CONF-9", "--reason", "conformance archive"],
    ["summary"],
    ["summary", "--all"],
    ["summary", "--archived"],
    ["archive", "--task", "CONF-9", "--unarchive"],
    ["stale", "--threshold-days", "0"],
    ["parked-aging", "--threshold-days", "0"],
    ["events", "--since", "2000-01-01T00:00:00Z"],
    ["detail", "--task", "CONF-1"],
    ["export"],
]

_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _mask_ids(node):
    """Integer `id` values are autoincrement artifacts (D1's counter is global
    per database, a local file's starts at 1) — opaque handles, masked out."""
    if isinstance(node, dict):
        return {k: ("<ID>" if k == "id" and isinstance(v, int) else _mask_ids(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_mask_ids(x) for x in node]
    return node


def normalize(text: str, op: str = ""):
    """Timestamps + integer ids masked, then parsed — we compare STRUCTURE, not
    formatting. Export tables compare as SETS (row order is insertion-order
    locally vs created_at-order on the hub; import doesn't care)."""
    masked = _TS.sub("<TS>", text)
    try:
        data = json.loads(masked)
    except ValueError:
        return masked  # error strings compare as text
    data = _mask_ids(data)
    if op == "export" and isinstance(data, dict):
        data = {table: sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
                for table, rows in data.items()}
    return data


def run_op(workspace: Path, op_args: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        ["pwc", "--workspace", str(workspace), *op_args],
        capture_output=True, text=True, timeout=60,
    )
    out = result.stdout if result.returncode == 0 else f"ERR: {result.stderr.strip()}"
    return result.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub-url", required=True)
    ap.add_argument("--hub-workspace", default="conformance-test")
    ap.add_argument("--token-file", default="~/.config/pwc/hub-token")
    args = ap.parse_args()

    base = Path(tempfile.mkdtemp(prefix="pwc-conformance-"))
    local_ws, hub_ws = base / "local", base / "hub"
    for ws in (local_ws, hub_ws):
        (ws / ".pwc").mkdir(parents=True)
    (hub_ws / ".pwc" / "store.json").write_text(json.dumps({
        "store": "hub", "url": args.hub_url,
        "workspace": args.hub_workspace, "token_file": args.token_file,
    }))
    subprocess.run(["pwc", "--workspace", str(local_ws), "init"],
                   capture_output=True, check=True)

    # Refuse a dirty hub workspace — the sequence assumes a blank slate.
    rc, out = run_op(hub_ws, ["summary", "--all"])
    if rc != 0:
        print(f"cannot reach hub workspace: {out}", file=sys.stderr)
        return 2
    if json.loads(out):
        print(f"hub workspace {args.hub_workspace!r} is not empty — aborting",
              file=sys.stderr)
        return 2

    failures = 0
    for op_args in SEQUENCE:
        rc_l, out_l = run_op(local_ws, op_args)
        rc_h, out_h = run_op(hub_ws, op_args)
        label = " ".join(op_args[:3])
        op = op_args[0]
        if (rc_l != rc_h) or (normalize(out_l, op) != normalize(out_h, op)):
            failures += 1
            print(f"✗ DIVERGENCE: {label}")
            print(f"  local (rc {rc_l}): {json.dumps(normalize(out_l, op), default=str)[:400]}")
            print(f"  hub   (rc {rc_h}): {json.dumps(normalize(out_h, op), default=str)[:400]}")
        else:
            print(f"✓ {label}")

    shutil.rmtree(base, ignore_errors=True)
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} — "
          f"{len(SEQUENCE) - failures}/{len(SEQUENCE)} ops identical"
          f"{' (hub test workspace left populated; delete via dashboard or reuse a fresh name)' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
