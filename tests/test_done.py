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


def test_close_issue_idempotent():
    view = mock.Mock(returncode=0, stdout=json.dumps({"state": "CLOSED"}), stderr="")
    with mock.patch.object(done.subprocess, "run", return_value=view) as run:
        assert done.close_issue("a/b", "1", "ok") == "already-closed"
    assert run.call_count == 1
