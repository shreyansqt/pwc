#!/usr/bin/env python3
"""Open a PWC session from the current workspace.

`pwc resume` lists every session on the board and lets you pick one.
`pwc continue` opens the most recent one, with no prompt. The workspace is
the nearest directory with `.pwc`, wherever you are inside it.

A live worker is attached, not duplicated: remote is `ssh -t <host> tmux
attach`, local focuses the iTerm tab. A dead worker is resumed with the
harness and model that ran it. A stuck worker (still alive, input wedged)
is restarted with `r<number>` or `--restart`: the process is killed, then
the same session resumes.

The directory resumed in is the one the session was started in. That is the
cwd stored on the session, or, when that was never recorded, the directory
recovered from the Claude transcript. The runhost's `workspace_root` join is
a fresh-dispatch rule; a remote tree does not have to mirror the laptop, so
resume does not recompute it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import spawn
import worker_status
from _common import emit, fail, find_workspace_root, store_config

_SSH_OPTS = spawn._SSH_OPTS
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

# Runs locally, or on the runhost over `ssh host python3 - SESSION_ID`.
# Prints the cwd, nothing if the transcript is not there, or every match and
# exit 2 when more than one directory slugs to the same transcript folder.
# PWC_DECODE_ROOT overrides the walk root (tests); production walks `/`.
_FIND_CLAUDE_CWD = r"""
import os, sys
from pathlib import Path

def session_slug(cwd):
    return str(Path(cwd).resolve()).replace("/", "-")

def walk(directory, rest):
    if not rest:
        return []
    found = []
    try:
        children = list(directory.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        name = child.name
        if rest == name:
            found.append(child)
        elif rest.startswith(name + "-"):
            found.extend(walk(child, rest[len(name) + 1:]))
    return found

def decode(slug, root):
    if not slug.startswith("-"):
        return []
    logical = walk(root, slug[1:])
    if str(root) != "/":
        return logical
    confirmed = [p for p in logical if session_slug(str(p)) == slug]
    return confirmed or logical

def main():
    if len(sys.argv) != 2:
        print("usage: find-cwd SESSION_ID", file=sys.stderr)
        return 1
    session_id = sys.argv[1]
    if (not session_id or "/" in session_id or ".." in session_id
            or not session_id.replace("-", "").replace("_", "").isalnum()):
        print("bad session id", file=sys.stderr)
        return 1
    projects = Path.home() / ".claude" / "projects"
    slugs = []
    if projects.is_dir():
        try:
            children = list(projects.iterdir())
        except OSError:
            children = []
        for child in children:
            if (child / f"{session_id}.jsonl").is_file():
                slugs.append(child.name)
    root = Path(os.environ.get("PWC_DECODE_ROOT", "/"))
    found = []
    for slug in slugs:
        for path in decode(slug, root):
            if path not in found:
                found.append(path)
    if len(found) == 1:
        print(found[0])
        return 0
    if len(found) > 1:
        for path in found:
            print(path)
        return 2
    return 0

sys.exit(main())
"""


def format_when(iso: str | None) -> str:
    """`22 Oct 11:39` in local time. Empty input stays a dash."""
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    return f"{local.day} {local.strftime('%b %H:%M')}"


def activity_at(started_at: str | None, last_event_at: str | None) -> str:
    """Later of the session start and the task's last event. ISO strings sort."""
    return max(started_at or "", last_event_at or "")


def resolve_runner(session: dict, events: list[dict], task: dict) -> tuple[str | None, str | None]:
    """Harness and model that ran this session.

    The session row wins. Then the timeline (a FORCED note, else the routed
    event). Then a pin stored on the task.
    """
    if session.get("harness"):
        return session.get("harness"), session.get("model")
    inferred = spawn.runner_from_events(events)
    if inferred:
        return inferred
    if task.get("harness") and task.get("model"):
        return task["harness"], task["model"]
    return None, None


def parse_choice(text: str, count: int) -> tuple[str, int] | None:
    """`3` opens row 3, `r3` / `r 3` restarts it, `q` quits.

    Returns ('open'|'restart', index) with index 0-based, or None to quit.
    Raises ValueError when the text is not a choice.
    """
    raw = text.strip().lower()
    if raw in {"q", "quit"}:
        return None
    restart = False
    body = raw
    if body.startswith("r"):
        restart = True
        body = body[1:].strip()
    if not body.isdigit():
        raise ValueError(text)
    number = int(body)
    if not 1 <= number <= count:
        raise ValueError(text)
    return ("restart" if restart else "open", number - 1)


def _short(text: str | None, limit: int = 72) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def format_table(rows: list[dict], workspace_name: str) -> str:
    """The picker. One session per numbered row, most recent first."""
    headers = ("#", "last activity", "state", "task", "dir", "harness/model", "host")
    body = []
    for i, row in enumerate(rows, 1):
        runner = row.get("harness") or "?"
        if row.get("model"):
            runner = f"{row.get('harness')}/{row['model']}"
        body.append((
            str(i),
            row.get("when") or "-",
            row.get("state") or "?",
            row["task_id"],
            row.get("workdir") or ".",
            runner,
            row.get("runhost") or "local",
            _short(row.get("title")),
        ))
    widths = [len(h) for h in headers]
    for item in body:
        for col in range(len(headers)):
            widths[col] = max(widths[col], len(item[col]))
    def line(cells):
        return "  ".join(cells[col].ljust(widths[col]) for col in range(len(headers)))
    lines = [
        f"{workspace_name} — {len(rows)} session{'s' if len(rows) != 1 else ''}",
        line(headers),
    ]
    for item in body:
        lines.append(line(item[:7]))
        if item[7]:
            lines.append(f"     {item[7]}")
    return "\n".join(lines)


def load_sessions(workspace: Path) -> list[dict]:
    """Every session on the board, most recently active first."""
    store = store_config(workspace)
    if store.get("store") == "hub":
        import hub_client
        dump = hub_client.call("export", {}, store)
        tasks = {t["id"]: t for t in (dump.get("tasks") or []) if t.get("id")}
        events_by_task: dict[str, list] = {}
        for ev in dump.get("events") or []:
            events_by_task.setdefault(ev.get("task_id"), []).append(ev)
        raw = list(dump.get("task_sessions") or [])
    else:
        import pwc_db
        conn = pwc_db.connect(workspace)
        try:
            tasks = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}
            events_by_task = {}
            for r in conn.execute(
                "SELECT id, task_id, at, source, kind, detail FROM events "
                "ORDER BY at, id"
            ):
                events_by_task.setdefault(r["task_id"], []).append(dict(r))
            raw = [dict(r) for r in conn.execute(
                "SELECT task_id, session_id, harness, model, cwd, started_at "
                "FROM task_sessions"
            )]
        finally:
            conn.close()
    for events in events_by_task.values():
        events.sort(key=lambda ev: (ev.get("at") or "", ev.get("id") or 0))

    rows = []
    for session in raw:
        task = tasks.get(session.get("task_id"))
        if task is None or not session.get("session_id"):
            continue
        events = events_by_task.get(session["task_id"], [])
        harness, model = resolve_runner(session, events, task)
        started = session.get("started_at") or ""
        activity = activity_at(started, task.get("last_event_at"))
        rows.append({
            "task_id": task["id"],
            "title": task.get("title") or "",
            "session_id": session["session_id"],
            "harness": harness,
            "model": model,
            "cwd": session.get("cwd"),
            "started_at": started,
            "activity": activity,
            "when": format_when(activity),
            "workdir": task.get("workdir"),
            "runhost": task.get("runhost"),
            "status": task.get("status"),
        })
    rows.sort(key=lambda r: (r["activity"], r["started_at"], r["session_id"]),
              reverse=True)
    return rows


def _runhosts(workspace: Path) -> dict:
    import sources
    data = sources._load(str(workspace))
    hosts = data.get("runhosts") or {}
    return hosts if isinstance(hosts, dict) else {}


def _remote_states(ssh: str, session_ids: list[str]) -> dict[str, str]:
    """One ssh per host. Unreachable is not dead: the worker may still be up."""
    if not session_ids:
        return {}
    parts = []
    for sid in session_ids:
        if not _SAFE_ID.match(sid):
            continue
        quoted = shlex.quote(sid)
        parts.append(
            f"if pgrep -f {quoted} >/dev/null; then echo {quoted} alive; "
            f"else echo {quoted} dead; fi"
        )
    if not parts:
        return {sid: "dead" for sid in session_ids}
    try:
        result = subprocess.run(
            ["ssh", *_SSH_OPTS, ssh, "; ".join(parts)],
            capture_output=True, text=True, timeout=25,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {sid: "unreachable" for sid in session_ids}
    if result.returncode != 0:
        return {sid: "unreachable" for sid in session_ids}
    states = {}
    for line in result.stdout.splitlines():
        bits = line.split()
        if len(bits) == 2 and bits[1] in ("alive", "dead"):
            states[bits[0]] = bits[1]
    for sid in session_ids:
        states.setdefault(sid, "unreachable")
    return states


def annotate_liveness(rows: list[dict], workspace: Path) -> None:
    """Set row['state'] and row['ssh'] in place."""
    hosts = _runhosts(workspace)
    groups: dict[str | None, list[dict]] = {}
    for row in rows:
        ssh = None
        if row.get("runhost"):
            host = hosts.get(row["runhost"]) or {}
            ssh = host.get("ssh") or row["runhost"]
        row["ssh"] = ssh
        groups.setdefault(ssh, []).append(row)
    for ssh, group in groups.items():
        ids = [r["session_id"] for r in group]
        if ssh:
            states = _remote_states(ssh, ids)
        else:
            states = {
                sid: ("alive" if worker_status.is_alive(sid) else "dead")
                for sid in ids
            }
        for row in group:
            row["state"] = states.get(row["session_id"], "dead")


def find_claude_cwd(session_id: str, *, ssh: str | None = None,
                    home: str | None = None,
                    decode_root: str | None = None) -> tuple[str | None, str | None]:
    """(cwd, error) from a Claude transcript folder.

    The folder name is the cwd with slashes turned into dashes, which is
    ambiguous when a path component itself contains a dash. The walk follows
    real directories, so `side-projects/pwc` and a sibling `side-projects-pwc`
    stay distinct. (None, None) means no transcript. An error string means the
    lookup itself failed, or more than one directory matched.
    """
    if not _SAFE_ID.match(session_id or ""):
        return None, f"session id {session_id!r} is not safe to look up"
    env = os.environ.copy()
    if home:
        env["HOME"] = home
    if decode_root:
        env["PWC_DECODE_ROOT"] = decode_root
    if ssh:
        argv = ["ssh", *_SSH_OPTS, ssh, "python3", "-", session_id]
        run_env = None  # the remote home and filesystem, not ours
    else:
        argv = [sys.executable, "-", session_id]
        run_env = env
    try:
        result = subprocess.run(
            argv, input=_FIND_CLAUDE_CWD, capture_output=True, text=True,
            timeout=30, env=run_env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return None, f"transcript lookup failed: {exc}"
    stdout = result.stdout.strip()
    if result.returncode == 2:
        return None, "session transcript matches more than one directory:\n" + stdout
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:300]
        where = f" on {ssh}" if ssh else ""
        return None, (f"transcript lookup failed{where}: "
                      f"{detail or 'exit ' + str(result.returncode)}")
    if not stdout:
        return None, None
    return stdout.splitlines()[0], None


def lookup_cwd(row: dict, workspace: Path) -> tuple[str | None, str | None, str | None]:
    """(cwd, source, error). Source is stored, transcript, or workdir.

    A Claude session, and any remote session, does not fall back to joining
    the runhost workspace root: that join is how a fresh dispatch picks a
    directory, and the transcript was keyed by the directory actually used.
    """
    if row.get("cwd"):
        return row["cwd"], "stored", None
    remote = bool(row.get("ssh"))
    harness = row.get("harness")
    if harness in (None, "claude") or remote:
        found, err = find_claude_cwd(row["session_id"], ssh=row.get("ssh"))
        if found:
            return found, "transcript", None
        if err or harness == "claude" or remote:
            where = f" on {row['ssh']}" if row.get("ssh") else ""
            return None, None, err or (
                f"no cwd stored for session {row['session_id']} of {row['task_id']}, "
                f"and no claude transcript for it{where}. Resume needs the "
                f"directory the session was started in."
            )
    workdir = row.get("workdir")
    if workdir and workdir not in (".",):
        return str((workspace / workdir).resolve()), "workdir", None
    return str(workspace.resolve()), "workdir", None


def resolve_cwd(row: dict, workspace: Path) -> tuple[str, str]:
    """(cwd, source) for a resume. Fails when the directory cannot be known."""
    cwd, source, err = lookup_cwd(row, workspace)
    if not cwd:
        fail(err or f"no cwd for session {row.get('session_id')}")
    return cwd, source


def _title_has_task(blob: str, task_id: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(task_id)}(?![\w-])", blob) is not None


def focus_local_tab(task_id: str) -> bool:
    """Bring the iTerm tab for this task to the front. False if none matches."""
    try:
        import iterm2
    except ImportError:
        fail("iterm2 module not installed — run `pip install iterm2`")
    found = {"ok": False}

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        for window in app.windows or []:
            for tab in window.tabs or []:
                titles = []
                for session in tab.sessions or []:
                    if getattr(session, "name", None):
                        titles.append(session.name)
                for variable in ("titleOverride", "title"):
                    try:
                        value = await tab.async_get_variable(variable)
                    except Exception:  # noqa: BLE001 — title lookup is best-effort
                        value = None
                    if value:
                        titles.append(str(value))
                if _title_has_task("\n".join(titles), task_id):
                    await tab.async_activate(order_window_front=True)
                    found["ok"] = True
                    return

    try:
        iterm2.run_until_complete(_main)
    except Exception as exc:  # noqa: BLE001 — same surface as spawn
        fail(
            "could not reach iTerm2's Python API "
            "(is iTerm2 running with the API enabled in "
            f"Preferences -> General -> Magic?): {type(exc).__name__}: {exc}"
        )
    return found["ok"]


def stop_remote(row: dict, *, kill_process: bool) -> None:
    """Clear a remote tmux session so the next spawn actually launches.

    `tmux new-session -A` attaches to a leftover session and ignores the new
    command. A dead worker can leave that session behind. Killing the process
    is only for a stuck worker that pgrep still sees.
    """
    ssh = row["ssh"]
    name = spawn._tmux_name(row["task_id"])
    subprocess.run(
        ["ssh", *_SSH_OPTS, ssh, f"tmux kill-session -t {shlex.quote(name)}"],
        capture_output=True, text=True, timeout=25,
    )
    if kill_process and _SAFE_ID.match(row["session_id"]):
        subprocess.run(
            ["ssh", *_SSH_OPTS, ssh, f"pkill -f {shlex.quote(row['session_id'])}"],
            capture_output=True, text=True, timeout=25,
        )


def stop_local(session_id: str) -> None:
    if _SAFE_ID.match(session_id):
        subprocess.run(["pkill", "-f", session_id], capture_output=True, text=True)


def spawn_argv(row: dict, cwd: str, workspace: Path, *, dry_run: bool) -> list[str]:
    argv = [
        "--task", row["task_id"],
        "--cwd", cwd,
        "--session-id", row["session_id"],
        "--resume",
    ]
    if row.get("ssh"):
        argv += ["--ssh", row["ssh"], "--runhost", row.get("runhost") or row["ssh"],
                 "--local-root", str(workspace)]
    if dry_run:
        argv.append("--dry-run")
    return argv


def capture_spawn(argv: list[str]) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        spawn.main(argv)
    text = buf.getvalue().strip()
    return json.loads(text) if text else {}


def decide(row: dict, *, restart: bool) -> str:
    """attach, resume, or restart. Unreachable refuses both attach and resume."""
    state = row.get("state")
    if state == "unreachable":
        fail(
            f"{row.get('runhost') or row.get('ssh')} is unreachable — "
            f"not attaching or resuming {row['task_id']} "
            f"(the worker may still be running there)"
        )
    if restart:
        return "restart"
    if state == "alive":
        return "attach"
    return "resume"


def build_plan(row: dict, workspace: Path, *, restart: bool, dry_run: bool) -> dict:
    action = decide(row, restart=restart)
    plan = {
        "action": action,
        "task": row["task_id"],
        "title": row.get("title"),
        "session_id": row["session_id"],
        "harness": row.get("harness"),
        "model": row.get("model"),
        "state": row.get("state"),
        "workdir": row.get("workdir"),
        "runhost": row.get("runhost"),
        "ssh": row.get("ssh"),
    }
    if action == "attach":
        if row.get("ssh"):
            name = spawn._tmux_name(row["task_id"])
            plan["command"] = f"ssh -t {shlex.quote(row['ssh'])} tmux attach -t {name}"
        else:
            plan["command"] = f"focus iTerm tab for {row['task_id']}"
        if dry_run:
            cwd, source, err = lookup_cwd(row, workspace)
            plan["cwd"] = cwd
            plan["cwd_source"] = source
            if err:
                plan["cwd_error"] = err
        return plan

    if not row.get("harness"):
        fail(
            f"cannot tell which harness ran session {row['session_id']} "
            f"of {row['task_id']}. The session row, the FORCED model notes, "
            f"and the routed events are all empty."
        )
    cwd, source = resolve_cwd(row, workspace)
    plan["cwd"] = cwd
    plan["cwd_source"] = source
    argv = spawn_argv(row, cwd, workspace, dry_run=True)
    plan["spawn_argv"] = argv
    plan["spawn"] = capture_spawn(argv)
    plan["command"] = plan["spawn"].get("command")
    if action == "restart" or row.get("ssh"):
        if row.get("ssh"):
            plan["stop"] = f"tmux kill-session -t {spawn._tmux_name(row['task_id'])}"
            if action == "restart":
                plan["stop"] += f"; pkill -f {row['session_id']}"
        elif action == "restart":
            plan["stop"] = f"pkill -f {row['session_id']}"
    return plan


def execute(plan: dict, row: dict, workspace: Path, *, dry_run: bool) -> None:
    if dry_run:
        emit(plan)
        return
    action = plan["action"]
    if action == "attach":
        if row.get("ssh"):
            name = spawn._tmux_name(row["task_id"])
            print(f"attaching to {name} on {row['ssh']}", file=sys.stderr)
            os.execvp("ssh", ["ssh", "-t", row["ssh"], "tmux", "attach", "-t", name])
        print(f"focusing the iTerm tab for {row['task_id']}", file=sys.stderr)
        if not focus_local_tab(row["task_id"]):
            fail(
                f"{row['task_id']} is alive but no iTerm tab titled with it "
                f"was found. Not starting a second worker."
            )
        return
    if row.get("ssh"):
        stop_remote(row, kill_process=(action == "restart"))
    elif action == "restart":
        stop_local(row["session_id"])
    print(
        f"resuming {row['task_id']} ({row.get('harness')}/{row.get('model')}) "
        f"in {plan['cwd']}",
        file=sys.stderr,
    )
    spawn.main(spawn_argv(row, plan["cwd"], workspace, dry_run=False))


def _workspace_name(workspace: Path) -> str:
    store = store_config(workspace)
    return store.get("workspace") or workspace.name


def _require_workspace(start: str | None) -> Path:
    root = find_workspace_root(start)
    if not (root / ".pwc").exists():
        fail(f"no PWC workspace from {start or os.getcwd()} — "
             f"cd into a workspace or pass --workspace")
    return root


def select_row(rows: list[dict], args) -> tuple[dict | None, bool]:
    """The session to open, and whether to restart it. None lists and stops."""
    matched = rows
    if args.task:
        matched = [r for r in rows if r["task_id"] == args.task]
        if not matched:
            fail(f"no PWC session for task {args.task!r} in this workspace")
    if args.choice:
        try:
            parsed = parse_choice(args.choice, len(matched))
        except ValueError:
            fail(f"not a session choice: {args.choice!r}")
        if parsed is None:
            return None, False
        kind, index = parsed
        return matched[index], kind == "restart" or args.restart
    if args.continue_latest or args.task:
        if not matched:
            fail("no PWC sessions in this workspace")
        return matched[0], args.restart
    if args.dry_run or not sys.stdin.isatty():
        return None, False
    print(format_table(rows, _workspace_name(find_workspace_root(args.workspace))))
    while True:
        try:
            text = input("number to open, r<number> to restart a stuck worker, q to quit: ")
        except EOFError:
            return None, False
        try:
            parsed = parse_choice(text, len(rows))
        except ValueError:
            print("enter a number, r and a number, or q")
            continue
        if parsed is None:
            return None, False
        kind, index = parsed
        return rows[index], kind == "restart" or args.restart


def list_payload(rows: list[dict]) -> list[dict]:
    payload = []
    for i, row in enumerate(rows, 1):
        payload.append({
            "index": i,
            "task": row["task_id"],
            "title": row.get("title"),
            "session_id": row["session_id"],
            "harness": row.get("harness"),
            "model": row.get("model"),
            "workdir": row.get("workdir"),
            "runhost": row.get("runhost"),
            "state": row.get("state"),
            "activity": row.get("activity"),
        })
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pwc resume",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", help="workspace root (default: discover from cwd)")
    parser.add_argument("--continue", dest="continue_latest", action="store_true",
                        help="open the most recent session, with no prompt")
    parser.add_argument("--task", help="skip the list; use this task's latest session")
    parser.add_argument("--choice", help="picker selection without a terminal (1, r2, q)")
    parser.add_argument("--restart", action="store_true",
                        help="kill a stuck worker and resume the same session")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the action as JSON; do not attach or spawn")
    args = parser.parse_args(argv)

    workspace = _require_workspace(args.workspace)
    rows = load_sessions(workspace)
    if not rows:
        fail("no PWC sessions in this workspace")
    annotate_liveness(rows, workspace)

    row, restart = select_row(rows, args)
    if row is None:
        if args.dry_run:
            emit(list_payload(rows))
        elif not sys.stdin.isatty():
            print(format_table(rows, _workspace_name(workspace)))
            print("not a terminal — pass --task, a --choice, or run pwc continue",
                  file=sys.stderr)
        return
    plan = build_plan(row, workspace, restart=restart, dry_run=args.dry_run)
    execute(plan, row, workspace, dry_run=args.dry_run)


if __name__ == "__main__":
    main(sys.argv[1:])
