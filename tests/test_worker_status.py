"""T3 liveness: a settled thread is not running; a pending request is waiting."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import worker_status  # noqa: E402


def _proc(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=["t3"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def _show(thread: dict, returncode=0, stderr=""):
    body = json.dumps({"thread": thread})
    return _proc(stdout=body, stderr=stderr, returncode=returncode)


def test_settled_thread_is_not_running():
    """The SMT-1477 shape: completed, settled, nothing pending, no active run."""
    alive, waiting = worker_status._classify_t3_thread({
        "status": "completed",
        "settled": True,
        "settledAt": "2026-11-17T14:01:53.892Z",
        "activeRunId": None,
        "pendingRequestCount": 0,
        "archived": False,
    })
    assert (alive, waiting) == (False, False)


def test_running_thread_is_alive():
    alive, waiting = worker_status._classify_t3_thread({
        "status": "running",
        "settled": False,
        "activeRunId": "run:1",
        "pendingRequestCount": 0,
        "archived": False,
    })
    assert (alive, waiting) == (True, False)


def test_pending_request_is_waiting_not_dead():
    alive, waiting = worker_status._classify_t3_thread({
        "status": "running",
        "settled": False,
        "activeRunId": "run:7",
        "pendingRequestCount": 1,
        "archived": False,
    })
    assert (alive, waiting) == (True, True)


def test_pending_request_wins_over_a_settled_flag():
    """T3 does not settle a thread that still has a pending request. If both
    flags show up, the question must not look like a process that exited."""
    alive, waiting = worker_status._classify_t3_thread({
        "settled": True,
        "pendingRequestCount": 1,
        "archived": False,
    })
    assert (alive, waiting) == (True, True)


def test_completed_but_not_settled_stays_alive():
    """Liveness follows `settled`, not `status`. T3 withholds settled while
    something is still open."""
    alive, waiting = worker_status._classify_t3_thread({
        "status": "completed",
        "settled": False,
        "activeRunId": None,
        "pendingRequestCount": 0,
        "archived": False,
    })
    assert (alive, waiting) == (True, False)


def test_archived_thread_is_not_running_even_with_a_pending_request():
    alive, waiting = worker_status._classify_t3_thread({
        "settled": False,
        "pendingRequestCount": 2,
        "archived": True,
    })
    assert (alive, waiting) == (False, False)
    alive, waiting = worker_status._classify_t3_thread({"archivedAt": "2026-11-17T00:00:00Z"})
    assert (alive, waiting) == (False, False)


def test_show_json_reports_settled_and_waiting(monkeypatch, capsys):
    answers = {
        "settled-thread": _show({
            "settled": True,
            "pendingRequestCount": 0,
            "status": "completed",
            "activeRunId": None,
        }),
        "waiting-thread": _show({
            "settled": False,
            "pendingRequestCount": 1,
            "status": "running",
        }),
    }

    def fake_run(cmd, **kwargs):
        assert cmd[0:3] == ["t3", "thread", "show"]
        assert "--messages" in cmd and "0" in cmd
        return answers[cmd[3]]

    monkeypatch.setattr(worker_status.subprocess, "run", fake_run)
    worker_status.main([
        "--session-ids",
        "t3:settled-thread,t3:waiting-thread",
    ])
    rows = json.loads(capsys.readouterr().out)
    assert rows == [
        {"session_id": "t3:settled-thread", "alive": False},
        {"session_id": "t3:waiting-thread", "alive": True, "waiting": True},
    ]


def test_missing_thread_is_not_running(monkeypatch):
    monkeypatch.setattr(
        worker_status.subprocess, "run",
        lambda *a, **k: _proc(stderr="thread not found", returncode=1),
    )
    assert worker_status.is_alive("t3:gone") is False


def test_t3_timeout_is_unknown(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="t3", timeout=30)

    monkeypatch.setattr(worker_status.subprocess, "run", boom)
    assert worker_status._t3_thread_liveness("slow") == (None, False)
