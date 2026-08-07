"""Shared helpers: workspace-root discovery, db path resolution, time, output.

A PWC installation lives in a workspace directory (e.g. ~/workspaces/acme).
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


def discover_workspaces() -> list[Path]:
    """Every PWC workspace on this machine (any dir with a .pwc/).

    Kept shallow (~/workspaces/* and ~/*) rather than a full-disk walk: PWC workspaces
    are top-level bodies of work by definition, and a deep scan of $HOME is slow
    and would wander into node_modules.

    (Lived in cost.py first — cost attribution needed it because transcripts are
    MACHINE-wide while a board is per-workspace. It turns out the coordinator
    needs the same primitive, so it moved here; cost.py re-exports it.)
    """
    roots: list[Path] = []
    home = Path.home()
    seen = set()
    for parent in (home / "workspaces", home):
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in sorted(children):
            if (child / ".pwc").is_dir() and child not in seen:
                seen.add(child)
                roots.append(child)
    return roots


def is_workspace(root: str | os.PathLike[str]) -> bool:
    """Is `root` a REAL workspace — i.e. does it have somewhere to keep tasks?

    A bare `.pwc/` directory is not enough. `cost.py` writes its usage.db into
    `<root>/.pwc/`, so any directory a read op ran in can end up with a `.pwc/`
    holding nothing but usage.db — no task store at all. (This bit for real: the
    failing `pwc summary` from ~/workspaces created ~/workspaces/.pwc/, which
    then made ~/workspaces itself look like a workspace and masked the real
    ones below it.)

    A workspace is a place with a TASK STORE: a local taskdb.db, or a store.json
    pointing at one (e.g. a hub). Nothing else counts.
    """
    root = Path(root)
    pwc = root / ".pwc"
    if not pwc.is_dir():
        return False
    return (pwc / "taskdb.db").exists() or (pwc / "store.json").exists()


def workspaces_below(start: str | os.PathLike[str] | None = None) -> list[Path]:
    """PWC workspaces sitting one level BELOW `start` (default cwd).

    Discovery walks UP to find the workspace you're standing in. But standing in a
    PARENT of several workspaces (~/workspaces, holding smarta/ and side-projects/) is a
    real place to be — it's where you coordinate across them — and walking up from
    there finds nothing at all. So we also look down, one level, to answer "which
    workspaces does this directory contain?".

    One level only, deliberately: a workspace is a top-level body of work, and an
    unbounded descent would wander into every repo checkout below it.
    """
    here = Path(start or os.getcwd()).resolve()
    if is_workspace(here):
        return []  # standing IN a workspace — not a parent of them
    out: list[Path] = []
    try:
        children = sorted(here.iterdir())
    except OSError:
        return []
    for child in children:
        if child.is_dir() and is_workspace(child):
            out.append(child)
    return out


def workspace_name(root: str | os.PathLike[str]) -> str:
    """The workspace's logical name: store.json's `workspace`, else the dir name.

    A hub-backed workspace already declares its name (that's the key its rows are
    filed under in D1); a local one is just its directory.
    """
    root = Path(root)
    cfg = store_config(root)
    return cfg.get("workspace") or root.name


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


# Ops that only READ. From a multi-workspace directory these fan out across every
# workspace and merge; everything else is a write and must land in exactly one.
READ_OPS = frozenset({"summary", "stale", "parked-aging", "events", "detail",
                      "find-refs", "find-session", "sessions", "export"})


def resolve_task_workspace(task_id: str, roots: list[Path]) -> Path:
    """Which of `roots` holds `task_id`? Exactly one, or we refuse.

    Task ids are unique WITHIN a workspace, never across them — nothing has ever
    enforced otherwise, and a collision has actually happened (`pwc-routing-engine`
    existed on both the smarta and side-projects boards, the same work queued twice
    because the coordinator was standing in the wrong directory). So a write naming
    a bare id from a multi-workspace directory is genuinely ambiguous, and guessing
    is how you silently mutate the wrong board.

    Unique hit -> that workspace. No hit -> a clean "not found" naming where we
    looked. Several hits -> REFUSE and make the caller disambiguate with
    --workspace. Never pick one.
    """
    import subprocess
    hits: list[Path] = []
    for root in roots:
        try:
            out = subprocess.run(
                ["pwc", "--workspace", str(root), "detail", "--task", task_id],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            hits.append(root)
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(workspace_name(r) for r in roots)
    if not hits:
        fail(f"no task {task_id!r} in any workspace here ({names}). "
             f"Pass --workspace <dir> if it lives somewhere else.")
    where = "\n".join(f"  --workspace {r}   ({workspace_name(r)})" for r in hits)
    fail(f"task {task_id!r} exists in more than one workspace — refusing to guess "
         f"which one you mean. Disambiguate:\n{where}")


def multi_workspace_fail(roots: list[Path], cmd: str) -> None:
    """The 'you're in a parent, and this op must land in ONE workspace' diagnostic.

    Deliberately names the workspaces it can see and shows the exact flag, because
    the old message here pointed at a `.pwc/taskdb.db` that (post-hub-migration) no
    longer exists in any workspace — following it would create a local database PWC
    does not read.
    """
    lines = "\n".join(f"  pwc --workspace {r} {cmd} …   ({workspace_name(r)})"
                      for r in roots)
    fail(f"{cmd!r} creates something, so it has to land in ONE workspace — and "
         f"you're in a directory that holds several, with nothing to infer from. "
         f"Say which:\n{lines}")


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
