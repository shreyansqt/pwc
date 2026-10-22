"""pwc resume / pwc continue — workspace session list, attach vs resume, cwd."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BIN = Path(__file__).resolve().parent.parent / "bin" / "pwc"
TASKDB = SCRIPTS / "taskdb.py"
PYTHON = sys.executable
ENV = {**os.environ, "PYTHONPATH": str(SCRIPTS)}

sys.path.insert(0, str(SCRIPTS))
import resume  # noqa: E402
import spawn  # noqa: E402


def _run(*args, **kwargs):
    result = subprocess.run(
        [PYTHON, *args], capture_output=True, text=True,
        timeout=60, env=ENV, **kwargs,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _taskdb(workspace, *args):
    return _run(str(TASKDB), "--workspace", str(workspace), *args)


def _init(root: Path):
    (root / "nabu").mkdir()
    rc, out, err = _taskdb(root, "init")
    assert rc == 0, err
    return out


def _pwc(root: Path, *args):
    return _run(str(BIN), "--workspace", str(root), *args)


def test_parse_choice_opens_restarts_and_quits():
    assert resume.parse_choice("2", 3) == ("open", 1)
    assert resume.parse_choice("r1", 3) == ("restart", 0)
    assert resume.parse_choice("r 3", 3) == ("restart", 2)
    assert resume.parse_choice("q", 3) is None
    try:
        resume.parse_choice("9", 3)
    except ValueError:
        pass
    else:
        raise AssertionError("out of range must be rejected")


def test_force_note_beats_routed_event_for_nabu_shape():
    events = [
        {"kind": "routed", "detail":
         "cursor-agent/claude-sonnet-5-5-high — x is first in the fill order"},
        {"kind": "note", "detail":
         "FORCED model claude/sonnet over routed None/None: "
         "Resume a stuck TUI with the session's own harness: claude/sonnet"},
    ]
    assert spawn.runner_from_events(events) == ("claude", "sonnet")
    assert resume.resolve_runner(
        {"harness": None, "model": None}, events, {"harness": None, "model": None},
    ) == ("claude", "sonnet")
    assert resume.resolve_runner(
        {"harness": "agy", "model": "gemini-x"}, events, {},
    ) == ("agy", "gemini-x")


def test_transcript_walk_finds_ghar_not_the_runhost_join(tmp_path):
    """nabu ran in ~/ghar/nabu. The configured join is ~/projects/nabu."""
    root = tmp_path / "fs"
    (root / "Users/grumpyorange/ghar/nabu").mkdir(parents=True)
    (root / "Users/grumpyorange/projects/nabu").mkdir(parents=True)
    home = tmp_path / "home"
    slug = "-Users-grumpyorange-ghar-nabu"
    sid = "123abafa-3cdf-4978-a4b4-51452edb9fb0"
    proj = home / ".claude" / "projects" / slug
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text("{}\n")
    cwd, err = resume.find_claude_cwd(sid, home=str(home), decode_root=str(root))
    assert err is None, err
    assert cwd == str(root / "Users/grumpyorange/ghar/nabu")


def test_transcript_walk_keeps_a_hyphenated_directory(tmp_path):
    root = tmp_path / "fs"
    (root / "Users/shreyans/workspaces/side-projects/pwc").mkdir(parents=True)
    home = tmp_path / "home"
    slug = "-Users-shreyans-workspaces-side-projects-pwc"
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    proj = home / ".claude" / "projects" / slug
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text("{}\n")
    cwd, err = resume.find_claude_cwd(sid, home=str(home), decode_root=str(root))
    assert err is None, err
    assert cwd == str(root / "Users/shreyans/workspaces/side-projects/pwc")


def test_transcript_walk_reports_two_directories_that_slug_the_same(tmp_path):
    root = tmp_path / "fs"
    (root / "Users/shreyans/workspaces/side-projects/pwc").mkdir(parents=True)
    (root / "Users/shreyans/workspaces/side-projects-pwc").mkdir(parents=True)
    home = tmp_path / "home"
    slug = "-Users-shreyans-workspaces-side-projects-pwc"
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    proj = home / ".claude" / "projects" / slug
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text("{}\n")
    cwd, err = resume.find_claude_cwd(sid, home=str(home), decode_root=str(root))
    assert cwd is None
    assert err and "more than one" in err


def test_set_session_fills_harness_and_cwd_on_a_second_call(tmp_path):
    _init(tmp_path)
    sid = "11111111-2222-3333-4444-555555555555"
    rc, _, err = _taskdb(
        tmp_path, "add-task", "--task", "t", "--type", "build-or-feature-work",
        "--title", "t",
    )
    assert rc == 0, err
    rc, _, err = _taskdb(tmp_path, "set-session", "--task", "t", "--session-id", sid)
    assert rc == 0, err
    rc, _, err = _taskdb(
        tmp_path, "set-session", "--task", "t", "--session-id", sid,
        "--harness", "claude", "--model", "sonnet", "--cwd", "/remote/ghar/nabu",
    )
    assert rc == 0, err
    rc, out, err = _taskdb(tmp_path, "sessions", "--task", "t")
    assert rc == 0, err
    row = json.loads(out)[0]
    assert row["harness"] == "claude"
    assert row["model"] == "sonnet"
    assert row["cwd"] == "/remote/ghar/nabu"
    # Omitting harness on a later call must not wipe it.
    rc, _, err = _taskdb(
        tmp_path, "set-session", "--task", "t", "--session-id", sid,
        "--cwd", "/remote/ghar/nabu",
    )
    assert rc == 0, err
    rc, out, err = _taskdb(tmp_path, "sessions", "--task", "t")
    assert json.loads(out)[0]["harness"] == "claude"


def test_continue_uses_last_activity_not_dispatch_order(tmp_path):
    _init(tmp_path)
    for task, title in (("older", "the older dispatch"), ("newer", "dispatched later")):
        rc, _, err = _taskdb(
            tmp_path, "add-task", "--task", task, "--type", "build-or-feature-work",
            "--title", title, "--workdir", "nabu",
        )
        assert rc == 0, err
    rc, _, err = _taskdb(
        tmp_path, "set-session", "--task", "older",
        "--session-id", "11111111-1111-1111-1111-111111111111",
        "--harness", "agy", "--model", "gemini-x", "--cwd", str(tmp_path / "nabu"),
    )
    assert rc == 0, err
    rc, _, err = _taskdb(
        tmp_path, "set-session", "--task", "newer",
        "--session-id", "22222222-2222-2222-2222-222222222222",
        "--harness", "agy", "--model", "gemini-x", "--cwd", str(tmp_path / "nabu"),
    )
    assert rc == 0, err
    # Second-resolution timestamps collide if this is left to the clock.
    # The older dispatch was touched again later; continue follows that.
    import pwc_db
    conn = pwc_db.connect(tmp_path)
    conn.execute(
        "UPDATE task_sessions SET started_at = ? WHERE task_id = ?",
        ("2026-10-01T00:00:00Z", "older"),
    )
    conn.execute(
        "UPDATE tasks SET last_event_at = ? WHERE id = ?",
        ("2026-10-22T09:00:00Z", "older"),
    )
    conn.execute(
        "UPDATE task_sessions SET started_at = ? WHERE task_id = ?",
        ("2026-10-21T00:00:00Z", "newer"),
    )
    conn.execute(
        "UPDATE tasks SET last_event_at = ? WHERE id = ?",
        ("2026-10-20T00:00:00Z", "newer"),
    )
    conn.commit()
    conn.close()
    rc, out, err = _pwc(tmp_path, "continue", "--dry-run")
    assert rc == 0, err
    plan = json.loads(out)
    assert plan["task"] == "older"
    assert plan["action"] == "resume"
    assert plan["cwd_source"] == "stored"
    assert plan["harness"] == "agy"
    assert plan["spawn"]["mode"] == "resume"
    assert "11111111-1111-1111-1111-111111111111" in plan["command"]


def test_resume_dry_run_lists_sessions_without_opening_one(tmp_path):
    _init(tmp_path)
    rc, _, err = _taskdb(
        tmp_path, "add-task", "--task", "only", "--type", "build-or-feature-work",
        "--title", "Only session", "--workdir", "nabu",
    )
    assert rc == 0, err
    rc, _, err = _taskdb(
        tmp_path, "set-session", "--task", "only",
        "--session-id", "33333333-3333-3333-3333-333333333333",
        "--harness", "claude", "--model", "sonnet",
    )
    assert rc == 0, err
    rc, out, err = _pwc(tmp_path, "resume", "--dry-run")
    assert rc == 0, err
    rows = json.loads(out)
    assert rows[0]["task"] == "only"
    assert rows[0]["harness"] == "claude"
    assert rows[0]["model"] == "sonnet"
    assert rows[0]["workdir"] == "nabu"
    assert rows[0]["state"] == "dead"


def test_alive_remote_dry_run_attaches_tmux(tmp_path):
    row = {
        "task_id": "nabu-status-and-next",
        "title": "nabu status",
        "session_id": "123abafa-3cdf-4978-a4b4-51452edb9fb0",
        "harness": "claude",
        "model": "sonnet",
        "cwd": "/Users/grumpyorange/ghar/nabu",
        "workdir": "nabu",
        "runhost": "grumpyorange",
        "ssh": "grumpyorange",
        "state": "alive",
    }
    plan = resume.build_plan(row, tmp_path, restart=False, dry_run=True)
    assert plan["action"] == "attach"
    assert plan["command"] == (
        "ssh -t grumpyorange tmux attach -t pwc-nabu-status-and-next"
    )
    assert plan["cwd_source"] == "stored"


def test_restart_is_chosen_for_a_live_worker():
    row = {"state": "alive", "task_id": "t", "runhost": None, "ssh": None}
    assert resume.decide(row, restart=False) == "attach"
    assert resume.decide(row, restart=True) == "restart"
    assert resume.decide({**row, "state": "dead"}, restart=False) == "resume"
