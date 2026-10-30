import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import coord  # noqa: E402
import iterm_groups  # noqa: E402


def _row(model, rw, ops, cost_out):
    return {"harness": "claude", "model": model, "available": True,
            "cost_out": cost_out,
            "tiers": {"research-writing": rw, "ops-comms": ops}}


def test_tie_on_weakest_tier_breaks_on_total_then_price():
    rows = [_row("sonnet", 4, 4, 10.0), _row("opus", 5, 4, 20.0),
            _row("fable", 5, 4, 50.0)]
    with mock.patch.object(coord.models, "table", return_value={"models": rows}):
        model, _ = coord.pick_model("claude")
    assert model == "opus"


def test_weakest_tier_still_decides_first():
    rows = [_row("opus", 5, 3, 20.0), _row("sonnet", 4, 4, 10.0)]
    with mock.patch.object(coord.models, "table", return_value={"models": rows}):
        model, _ = coord.pick_model("claude")
    assert model == "sonnet"


def test_coord_groups_the_tab_before_replacing_the_shell(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(coord.models, "harness_available", lambda harness: True)
    monkeypatch.setattr(coord, "pick_model", lambda harness: ("opus", "because"))
    monkeypatch.setattr(coord, "shutil_which", lambda cmd: "/usr/bin/claude")
    order = []
    monkeypatch.setattr(
        coord, "_group_coordinator_tab",
        lambda workspace: order.append(("group", workspace)))

    def stop(cmd, argv):
        order.append(("exec", list(argv)))
        raise SystemExit(7)

    monkeypatch.setattr(coord.os, "execvp", stop)
    with pytest.raises(SystemExit) as raised:
        coord.main([])
    assert raised.value.code == 7
    assert order[0] == ("group", str(tmp_path.resolve()))
    assert order[1][0] == "exec"
    assert order[1][1][0] == "claude"


def test_print_command_does_not_group(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(coord.models, "harness_available", lambda harness: True)
    monkeypatch.setattr(coord, "pick_model", lambda harness: ("opus", "because"))
    called = []
    monkeypatch.setattr(
        coord, "_group_coordinator_tab", lambda workspace: called.append(workspace))
    assert coord.main(["--print-command"]) == 0
    assert called == []


def test_unsupported_iterm_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(iterm_groups, "current_tty", lambda: "/dev/ttys001")
    monkeypatch.setattr(
        iterm_groups, "group_tty", lambda workspace, tty: "unsupported")
    coord._group_coordinator_tab(str(tmp_path))
    assert "3.7.4" in capsys.readouterr().err


def test_no_tty_skips_grouping(tmp_path, monkeypatch):
    monkeypatch.setattr(iterm_groups, "current_tty", lambda: None)
    called = []
    monkeypatch.setattr(
        iterm_groups, "group_tty",
        lambda workspace, tty: called.append((workspace, tty)))
    coord._group_coordinator_tab(str(tmp_path))
    assert called == []


def test_machine_label_prefers_env_then_file_then_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(coord.spawn.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PWC_MACHINE_NAME", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "box.local")
    assert coord.spawn.machine_label() == "box"
    (tmp_path / ".config" / "pwc").mkdir(parents=True)
    (tmp_path / ".config" / "pwc" / "machine-name").write_text("mini\n")
    assert coord.spawn.machine_label() == "mini"
    monkeypatch.setenv("PWC_MACHINE_NAME", "env-name")
    assert coord.spawn.machine_label() == "env-name"


def test_coord_default_name_is_machine_pwc_workspace(tmp_path, monkeypatch, capsys):
    ws = tmp_path / "home-admin"
    ws.mkdir()
    monkeypatch.setenv("PWC_MACHINE_NAME", "mini")
    monkeypatch.setattr(coord.models, "harness_available", lambda harness: True)
    monkeypatch.setattr(coord, "pick_model", lambda harness: ("opus", "because"))
    assert coord.main(["--workspace", str(ws), "--print-command"]) == 0
    assert "--name 'mini · PWC · home-admin'" in capsys.readouterr().out


def test_claude_worker_session_name_carries_machine_prefix(monkeypatch):
    monkeypatch.setenv("PWC_MACHINE_NAME", "mini")
    _mode, args, _sid = coord.spawn._build_claude(
        session_id="abc", resume=False, cwd="/tmp", seed_prompt=None,
        model="sonnet", title="gh-ghar-13")
    assert args[args.index("--name") + 1] == "mini · gh-ghar-13"
