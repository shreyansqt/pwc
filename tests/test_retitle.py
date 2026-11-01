import sys
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import retitle  # noqa: E402
import spawn  # noqa: E402


def test_coordinator_name_matches_coord_default(tmp_path):
    ws = tmp_path / "side-projects"
    ws.mkdir()
    with mock.patch.object(spawn, "machine_label", return_value="mini"):
        res = retitle.compute(workspace=str(ws), task=None, harness="claude", model="opus")
    assert res["session_name"] == "mini · PWC · side-projects"
    assert res["rename_command"] == "/rename mini · PWC · side-projects"
    assert res["tab_title"] == "mini · PWC · side-projects [claude/opus]"


def test_worker_name_uses_task_row(tmp_path):
    ident = {"base": "T-1", "harness": "claude", "model": "sonnet"}
    with mock.patch.object(spawn, "machine_label", return_value="mini"), \
         mock.patch.object(retitle, "worker_identity", return_value=ident):
        res = retitle.compute(workspace=str(tmp_path), task="T-1", harness=None, model=None)
    assert res["rename_command"] == "/rename mini · T-1"
    assert res["tab_title"] == "T-1 [claude/sonnet]"


def test_non_claude_has_no_rename(tmp_path):
    ident = {"base": "T-2", "harness": "codex", "model": "gpt"}
    with mock.patch.object(retitle, "worker_identity", return_value=ident):
        res = retitle.compute(workspace=str(tmp_path), task="T-2", harness=None, model=None)
    assert res["rename_command"] is None and res["session_name"] is None
