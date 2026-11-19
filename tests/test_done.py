import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import done  # noqa: E402


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)


def test_preflight_clean_pushed_repo(tmp_path):
    remote, work = tmp_path / "r.git", tmp_path / "w"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "x")
    _git(work, "push", "-u", "origin", "HEAD")
    assert done.git_preflight(str(work)) == []
    (work / "f").write_text("x")
    assert "uncommitted" in done.git_preflight(str(work))[0]
    (work / "f").unlink()
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "y")
    assert "not pushed" in done.git_preflight(str(work))[0]


def test_preflight_non_repo(tmp_path):
    assert done.git_preflight(str(tmp_path)) == []


def test_tracker_issues_only_github():
    detail = {"refs": [
        {"ref_type": "github-issue", "value": "shreyansqt/pwc#20"},
        {"ref_type": "jira", "value": "ABC-1"},
        {"ref_type": "github-issue", "value": "garbage"}]}
    assert done.tracker_issues(detail) == [("shreyansqt/pwc", "20")]


def test_close_tab_skipped_outside_iterm(monkeypatch):
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    assert done.close_tab(None, 1).startswith("skipped")


def test_t3_session_recognizes_a_recorded_thread():
    assert done.t3_session({"task": {"session_id": "t3:abc"}})
    assert not done.t3_session({"task": {"session_id": "t3:"}})
    assert not done.t3_session({"task": {"session_id": "0199abc"}})
    assert not done.t3_session({"task": {}})


def test_t3_close_report_settles_and_does_not_archive():
    report = done.t3_close_report(no_close_tab=False, dry_run=False)
    assert report["t3"] == "settle"
    assert "archive" not in report["tab"]
    assert "action settle" in report["tab"]
    assert done.t3_close_report(no_close_tab=False, dry_run=True) == {
        "t3": "would-settle", "tab": "dry-run"}
    kept = done.t3_close_report(no_close_tab=True, dry_run=False)
    assert kept["t3"] == "kept (--no-close-tab)"


def _detail(session_id):
    return json.dumps({
        "task": {"status": "open", "session_id": session_id},
        "refs": [],
    })


def test_main_t3_settles_and_does_not_close_a_tab(monkeypatch, capsys):
    calls = []

    def fake_pwc(args, ws, check=True):
        calls.append(args)
        if args[0] == "detail":
            return _detail("t3:thread-1")
        return ""

    monkeypatch.setattr(done, "pwc", fake_pwc)
    monkeypatch.setattr(done, "close_tab", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a T3 worker has no tab to close")))
    rc = done.main(["--task", "T1", "--outcome", "shipped", "--skip-git-check"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["t3"] == "settle"
    assert report["status"] == "done"
    assert any(args[0] == "log-event" and "--set-status" in args for args in calls)
    assert not any(args[0] == "log-event" and "archive" in args for args in calls)


def test_main_t3_dry_run_does_not_settle_yet(monkeypatch, capsys):
    calls = []

    def fake_pwc(args, ws, check=True):
        calls.append(args)
        return _detail("t3:thread-1")

    monkeypatch.setattr(done, "pwc", fake_pwc)
    rc = done.main(["--task", "T1", "--outcome", "shipped", "--dry-run", "--skip-git-check"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["t3"] == "would-settle"
    assert report["tab"] == "dry-run"
    assert not any(args[0] == "log-event" for args in calls)


def test_main_t3_keep_session_does_not_settle(monkeypatch, capsys):
    monkeypatch.setattr(done, "pwc", lambda args, ws, check=True: (
        _detail("t3:thread-1") if args[0] == "detail" else ""))
    monkeypatch.setattr(done, "close_tab", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("kept session")))
    rc = done.main(["--task", "T1", "--outcome", "shipped", "--no-close-tab", "--skip-git-check"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["t3"] == "kept (--no-close-tab)"


def test_pwc_done_and_launcher_tell_the_worker_to_settle():
    root = Path(__file__).resolve().parent.parent
    done_skill = (root / "skills" / "pwc-done" / "SKILL.md").read_text()
    start_skill = (root / "skills" / "pwc-start-work" / "SKILL.md").read_text()
    assert 'action: "settle"' in done_skill
    assert 'action: "archive"' not in done_skill
    assert "Do not archive the thread." in done_skill
    assert "action `settle`" in start_skill
    assert "archives it in the" not in start_skill
    import spawn
    assert spawn.T3_ON_DONE == "settle"


def test_close_issue_idempotent():
    view = mock.Mock(returncode=0, stdout=json.dumps({"state": "CLOSED"}), stderr="")
    with mock.patch.object(done.subprocess, "run", return_value=view) as run:
        assert done.close_issue("a/b", "1", "ok") == "already-closed"
    assert run.call_count == 1
