"""Shared helpers: workspace-root discovery, db path resolution, time, output.

A PWC installation lives in a workspace directory (e.g. ~/work/acme).
The ledger is at <workspace>/.pwc/ledger.db. The workspace root is found by walking
up from a starting directory until a marker (.pwc/ or .claude/) is seen — so any
script run from anywhere inside the workspace resolves the same db.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import os
import sys
from pathlib import Path

# Markers that identify a workspace root, in priority order. .pwc means PWC is
# already initialized here; .claude means it's a Claude Code workspace (so .pwc
# belongs here once initialized).
_ROOT_MARKERS = (".pwc", ".claude")


def find_workspace_root(start: str | os.PathLike[str] | None = None) -> Path:
    """Walk up from `start` (default cwd) to the nearest workspace root.

    Falls back to `start` itself if no marker is found, so `init` can create
    .pwc/ in the current directory on a fresh workspace.
    """
    here = Path(start or os.getcwd()).resolve()
    for d in (here, *here.parents):
        for marker in _ROOT_MARKERS:
            if (d / marker).exists():
                return d
    return here


def db_path(workspace: str | os.PathLike[str] | None = None) -> Path:
    """Resolve <workspace>/.pwc/ledger.db. `workspace` overrides discovery."""
    root = Path(workspace).resolve() if workspace else find_workspace_root()
    return root / ".pwc" / "ledger.db"


def now_iso() -> str:
    """Current time, ISO8601 UTC, second precision (sorts lexically)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_ago_iso(days: float) -> str:
    """ISO8601 UTC timestamp `days` in the past — for staleness thresholds."""
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(obj) -> None:
    """Write one JSON value to stdout (the skill↔script contract)."""
    _json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def fail(msg: str, code: int = 1):
    """Diagnostic to stderr, then exit nonzero."""
    print(f"pwc: {msg}", file=sys.stderr)
    raise SystemExit(code)


def read_json_stdin():
    """Read a JSON body from stdin (for `--json -` write payloads)."""
    data = sys.stdin.read()
    if not data.strip():
        return {}
    return _json.loads(data)
