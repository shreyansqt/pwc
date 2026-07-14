"""Coordinating from a PARENT of several workspaces (~/work holding smarta/, side-projects/).

The safety property under test is the refusal: task ids are unique WITHIN a
workspace and nothing has ever enforced uniqueness across them — a collision has
actually happened in the wild (`pwc-routing-engine` on both boards, the same work
queued twice because the coordinator was standing in the wrong directory). So a
write naming a bare id from a multi-workspace directory MUST refuse rather than
pick a board.
"""

from __future__ import annotations

import pytest

import _common


def _mk_workspace(root, name="ws", *, store=False):
    """A directory that is a REAL workspace: it has somewhere to keep tasks."""
    pwc = root / name / ".pwc"
    pwc.mkdir(parents=True)
    if store:
        (pwc / "store.json").write_text('{"store": "hub", "url": "https://h", "workspace": "%s"}' % name)
    else:
        (pwc / "taskdb.db").write_text("")
    return root / name


def test_is_workspace_requires_a_task_store(tmp_path):
    """A bare .pwc/ is NOT a workspace — cost.py's usage.db creates those as litter."""
    bare = tmp_path / "bare"
    (bare / ".pwc").mkdir(parents=True)
    (bare / ".pwc" / "usage.db").write_text("")  # what a stray cost read leaves behind
    assert not _common.is_workspace(bare)

    real = _mk_workspace(tmp_path, "real")
    assert _common.is_workspace(real)

    hubbed = _mk_workspace(tmp_path, "hubbed", store=True)
    assert _common.is_workspace(hubbed)


def test_workspaces_below_finds_children(tmp_path):
    _mk_workspace(tmp_path, "alpha")
    _mk_workspace(tmp_path, "beta")
    (tmp_path / "not-a-workspace").mkdir()

    found = {p.name for p in _common.workspaces_below(tmp_path)}
    assert found == {"alpha", "beta"}


def test_workspaces_below_is_empty_inside_a_workspace(tmp_path):
    """Standing IN a workspace is not standing in a parent of them."""
    ws = _mk_workspace(tmp_path, "alpha")
    assert _common.workspaces_below(ws) == []


def test_workspaces_below_ignores_a_phantom_pwc(tmp_path):
    """The regression that hid both real workspaces.

    A failed `pwc summary` in ~/work created ~/work/.pwc/ (cost.py minting a home for
    usage.db), which then made ~/work look like a workspace — so the parent short-
    circuited and the two REAL workspaces beneath it were never seen.
    """
    (tmp_path / ".pwc").mkdir()
    (tmp_path / ".pwc" / "usage.db").write_text("")
    _mk_workspace(tmp_path, "alpha")
    _mk_workspace(tmp_path, "beta")

    found = {p.name for p in _common.workspaces_below(tmp_path)}
    assert found == {"alpha", "beta"}, "a phantom .pwc must not mask the real workspaces"


def test_resolve_refuses_on_a_collision(tmp_path, monkeypatch):
    """The whole point: an ambiguous id is REFUSED, never guessed."""
    a = _mk_workspace(tmp_path, "alpha")
    b = _mk_workspace(tmp_path, "beta")

    # Both workspaces claim the id.
    monkeypatch.setattr(_common, "workspace_name", lambda r: str(r).rsplit("/", 1)[-1])

    class _Hit:
        returncode = 0
        stdout = '{"task": {"id": "dupe"}}'

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Hit())

    with pytest.raises(SystemExit):
        _common.resolve_task_workspace("dupe", [a, b])


def test_resolve_returns_the_single_owner(tmp_path, monkeypatch):
    a = _mk_workspace(tmp_path, "alpha")
    b = _mk_workspace(tmp_path, "beta")
    monkeypatch.setattr(_common, "workspace_name", lambda r: str(r).rsplit("/", 1)[-1])

    class _Hit:
        returncode = 0
        stdout = '{"task": {"id": "only-in-alpha"}}'

    class _Miss:
        returncode = 1
        stdout = ""

    def fake_run(cmd, **kw):
        return _Hit() if str(a) in cmd else _Miss()

    monkeypatch.setattr("subprocess.run", fake_run)
    assert _common.resolve_task_workspace("only-in-alpha", [a, b]) == a


def test_resolve_fails_cleanly_when_nothing_owns_it(tmp_path, monkeypatch):
    a = _mk_workspace(tmp_path, "alpha")
    monkeypatch.setattr(_common, "workspace_name", lambda r: str(r).rsplit("/", 1)[-1])

    class _Miss:
        returncode = 1
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Miss())
    with pytest.raises(SystemExit):
        _common.resolve_task_workspace("ghost", [a])
