"""Shared helpers: workspace-root discovery, db path resolution, time, output.

A PWC installation lives in a workspace directory (e.g. ~/work/acme).
The task database is at <workspace>/.pwc/taskdb.db. The workspace root is found by
walking up from a starting directory until a marker (.pwc/ or .claude/) is seen — so
any script run from anywhere inside the workspace resolves the same db.
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

    `.pwc` (an initialized PWC root) takes priority over `.claude` across the
    *whole* ancestry, not just per-level: a worker running in a repo subdir that
    has its own `.claude/` (e.g. team-skills/, service-webapp/) must still resolve
    up to the real workspace root that holds `.pwc`, instead of stopping at the
    nearer `.claude`. Only when no `.pwc` exists anywhere up the tree do we fall
    back to the nearest `.claude` (a fresh workspace, so `init` can create `.pwc`
    there). Falls back to `start` itself if no marker is found at all.
    """
    here = Path(start or os.getcwd()).resolve()
    chain = (here, *here.parents)
    for marker in _ROOT_MARKERS:
        for d in chain:
            if (d / marker).exists():
                return d
    return here


def db_path(workspace: str | os.PathLike[str] | None = None) -> Path:
    """Resolve <workspace>/.pwc/taskdb.db. `workspace` overrides discovery."""
    root = Path(workspace).resolve() if workspace else find_workspace_root()
    return root / ".pwc" / "taskdb.db"


def config_path(workspace: str | os.PathLike[str] | None = None) -> Path:
    """Resolve <workspace>/.pwc/sources.json — the per-workspace sources config."""
    root = Path(workspace).resolve() if workspace else find_workspace_root()
    return root / ".pwc" / "sources.json"


def store_config(workspace: str | os.PathLike[str] | None = None) -> dict:
    """The workspace's task-store config from <workspace>/.pwc/store.json.

    Absent file = {"store": "local"} — today's behavior, forever the default.
    A hub-backed workspace looks like:
      {"store": "hub", "url": "https://…workers.dev", "workspace": "smarta",
       "token_file": "~/.config/pwc/hub-token"}
    Only the task DATABASE moves to the hub; sources.json (and store.json itself)
    stay local files — they're config, not state.
    """
    root = Path(workspace).resolve() if workspace else find_workspace_root()
    p = root / ".pwc" / "store.json"
    if not p.exists():
        return {"store": "local"}
    try:
        cfg = _json.loads(p.read_text())
    except (ValueError, OSError) as e:
        fail(f"could not read store config at {p}: {e}")
    if cfg.get("store") == "hub":
        for key in ("url", "workspace"):
            if not cfg.get(key):
                fail(f"store config at {p} is store=hub but missing {key!r}")
    return cfg


def model_table_path() -> Path:
    """The GLOBAL model table at ~/.config/pwc/model-table.json.

    Global, not per-workspace: which models exist, what they cost, and how good
    they are at what is a fact about the WORLD (and about this person's taste),
    not about one workspace's body of work. Every workspace routes off the same
    table. Overridable with $PWC_MODEL_TABLE (tests, alternate profiles).
    """
    env = os.environ.get("PWC_MODEL_TABLE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "pwc" / "model-table.json"


def ssl_context():
    """An SSL context that actually has CA certificates.

    Some python builds (notably MacPorts — the one on this machine) ship an EMPTY
    default trust store, so every HTTPS call fails closed with
    CERTIFICATE_VERIFY_FAILED. Fall back to the system CA bundle rather than
    disabling verification. Shared by every outbound caller (hub_client, models).
    """
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        bundle = Path("/etc/ssl/cert.pem")
        if bundle.exists():
            ctx = _ssl.create_default_context(cafile=str(bundle))
    return ctx


def now_iso() -> str:
    """Current time, ISO8601 UTC, second precision (sorts lexically)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str) -> _dt.datetime | None:
    """Parse an ISO8601 UTC stamp (trailing Z) back to an aware datetime, or None."""
    if not ts:
        return None
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def age_days(ts: str) -> float | None:
    """How many days ago `ts` was (None if unparseable) — the staleness clock."""
    t = parse_iso(ts)
    if t is None:
        return None
    return (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() / 86400.0


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
