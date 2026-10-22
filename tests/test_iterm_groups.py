"""Tab-group placement: one group per workspace, found by path then by name."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import iterm_groups  # noqa: E402


class FakeGroup:
    def __init__(self, group_id, name):
        self.group_id = group_id
        self.name = name


class FakeSession:
    def __init__(self, tty=None):
        self.tty = tty

    async def async_get_variable(self, name):
        if name == "tty" and self.tty:
            return self.tty
        raise RuntimeError(f"unset {name}")


class FakeTab:
    def __init__(self, tab_id, window, group=None, workspace=None, sessions=None):
        self.tab_id = tab_id
        self.window = window
        self.tab_group = group
        self.vars = {}
        if workspace is not None:
            self.vars[iterm_groups.VARIABLE] = workspace
        self.sessions = sessions or []
        self.added = None

    async def async_get_variable(self, name):
        if name not in self.vars:
            raise RuntimeError(f"unset {name}")
        return self.vars[name]

    async def async_set_variable(self, name, value):
        self.vars[name] = value

    async def async_add_to_tab_group(self, group):
        self.added = group
        self.tab_group = group


class FakeWindow:
    def __init__(self, window_id):
        self.window_id = window_id
        self.tabs = []
        self.created = None
        self.moved = None

    async def async_create_tab_group(self, name, tabs, color=None):
        group = FakeGroup("g-" + name, name)
        for tab in tabs:
            tab.tab_group = group
        self.created = (name, list(tabs), color)
        return group

    async def async_set_tabs(self, tabs):
        self.moved = list(tabs)
        for tab in tabs:
            previous = tab.window
            if previous is not None and previous is not self and tab in previous.tabs:
                previous.tabs.remove(tab)
            tab.window = self
            if tab not in self.tabs:
                self.tabs.insert(0, tab)

    async def async_create_tab(self, select=True):
        self.created_select = select
        tab = FakeTab(f"new-{self.window_id}", self)
        self.tabs.append(tab)
        return tab


class FakeApp:
    def __init__(self, windows, current=None):
        self.windows = windows
        self.current_terminal_window = current or (windows[0] if windows else None)
        self.refreshed = False

    async def async_refresh(self):
        self.refreshed = True

    def get_tab_by_id(self, tab_id):
        for window in self.windows:
            for tab in window.tabs:
                if tab.tab_id == tab_id:
                    return tab
        return None


@pytest.fixture
def supported(monkeypatch):
    monkeypatch.setattr(iterm_groups, "tab_groups_supported", lambda connection: True)


def _member(window, tab_id, group, workspace=None):
    tab = FakeTab(tab_id, window, group=group, workspace=workspace)
    window.tabs.append(tab)
    return tab


def test_group_name_is_the_directory_basename(tmp_path):
    root = tmp_path / "side-projects"
    root.mkdir()
    assert iterm_groups.group_name(str(root)) == "side-projects"
    assert iterm_groups.workspace_key(str(root)) == str(root.resolve())


def test_stamped_path_beats_a_same_named_group_elsewhere(tmp_path, supported):
    root = tmp_path / "side-projects"
    root.mkdir()
    key = str(root.resolve())
    decoy = FakeWindow("decoy")
    home = FakeWindow("home")
    _member(decoy, "d", FakeGroup("g-decoy", "side-projects"))
    _member(home, "h", FakeGroup("g-home", "other"), workspace=key)
    app = FakeApp([decoy, home])

    window, group = asyncio.run(iterm_groups.find_group(app, str(root)))

    assert window is home
    assert group.group_id == "g-home"


def test_name_matches_a_hand_made_group(tmp_path, supported):
    root = tmp_path / "smarta"
    root.mkdir()
    window = FakeWindow("w")
    _member(window, "t", FakeGroup("g-smarta", "smarta"))
    app = FakeApp([window])

    found = asyncio.run(iterm_groups.find_group(app, str(root)))

    assert found[0] is window
    assert found[1].group_id == "g-smarta"


def test_creates_a_group_from_the_tab_when_none_exists(tmp_path, supported):
    root = tmp_path / "home-admin"
    root.mkdir()
    window = FakeWindow("w")
    tab = FakeTab("coord", window)
    window.tabs.append(tab)
    app = FakeApp([window])

    status = asyncio.run(iterm_groups.ensure_member(app, tab, str(root), object()))

    assert status == "created"
    assert window.created[0] == "home-admin"
    assert window.created[2] is None  # iTerm2 picks the color
    assert tab.vars[iterm_groups.VARIABLE] == str(root.resolve())


def test_joins_an_existing_group_in_the_same_window(tmp_path, supported):
    root = tmp_path / "side-projects"
    root.mkdir()
    window = FakeWindow("w")
    group = FakeGroup("g1", "side-projects")
    _member(window, "coord", group, workspace=str(root.resolve()))
    worker = FakeTab("worker", window)
    window.tabs.append(worker)
    app = FakeApp([window])

    status = asyncio.run(iterm_groups.ensure_member(app, worker, str(root), object()))

    assert status == "joined"
    assert worker.added is group
    assert worker.vars[iterm_groups.VARIABLE] == str(root.resolve())


def test_already_a_member_is_left_in_place(tmp_path, supported):
    root = tmp_path / "side-projects"
    root.mkdir()
    window = FakeWindow("w")
    group = FakeGroup("g1", "side-projects")
    tab = _member(window, "coord", group, workspace=str(root.resolve()))
    app = FakeApp([window])

    status = asyncio.run(iterm_groups.ensure_member(app, tab, str(root), object()))

    assert status == "already"
    assert tab.added is None


def test_moves_a_tab_into_the_groups_window_before_joining(tmp_path, supported):
    root = tmp_path / "smarta"
    root.mkdir()
    home = FakeWindow("home")
    elsewhere = FakeWindow("elsewhere")
    group = FakeGroup("g1", "smarta")
    _member(home, "worker", group, workspace=str(root.resolve()))
    coord = FakeTab("coord", elsewhere)
    elsewhere.tabs.append(coord)
    app = FakeApp([home, elsewhere])

    status = asyncio.run(iterm_groups.ensure_member(app, coord, str(root), object()))

    assert status == "joined"
    assert app.refreshed
    assert coord.window is home
    assert coord.tab_group is group
    assert home.moved == [coord]


def test_unsupported_protocol_does_not_create_a_group(tmp_path, monkeypatch):
    monkeypatch.setattr(iterm_groups, "tab_groups_supported", lambda connection: False)
    root = tmp_path / "side-projects"
    root.mkdir()
    window = FakeWindow("w")
    tab = FakeTab("t", window)
    window.tabs.append(tab)

    status = asyncio.run(
        iterm_groups.ensure_member(FakeApp([window]), tab, str(root), object()))

    assert status == "unsupported"
    assert window.created is None


def test_new_tabs_open_in_the_groups_window(tmp_path, supported):
    root = tmp_path / "side-projects"
    root.mkdir()
    home = FakeWindow("home")
    current = FakeWindow("current")
    _member(home, "coord", FakeGroup("g1", "side-projects"),
            workspace=str(root.resolve()))
    app = FakeApp([current, home], current=current)

    window = asyncio.run(iterm_groups.window_for_workspace(app, str(root), current))

    assert window is home


def test_background_tab_falls_back_when_select_is_unknown():
    class OldWindow:
        def __init__(self):
            self.calls = []

        async def async_create_tab(self):
            self.calls.append("plain")
            return "tab"

    window = OldWindow()
    tab = asyncio.run(iterm_groups.create_background_tab(window))
    assert tab == "tab"
    assert window.calls == ["plain"]


def test_background_tab_asks_not_to_be_selected():
    window = FakeWindow("w")
    tab = asyncio.run(iterm_groups.create_background_tab(window))
    assert tab.window is window
    assert window.created_select is False


def test_tty_lookup_ignores_the_focused_tab_when_another_tty_is_reported():
    window = FakeWindow("w")
    other = FakeTab("other", window, sessions=[FakeSession("/dev/ttys001")])
    mine = FakeTab("mine", window, sessions=[FakeSession("/dev/ttys002")])
    window.tabs.extend([other, mine])
    app = FakeApp([window])
    app.current_terminal_window.current_tab = other

    found = asyncio.run(iterm_groups.find_tab_by_tty(app, "/dev/ttys002"))

    assert found is mine


def test_tty_lookup_uses_the_focused_tab_when_no_session_reports_a_tty():
    window = FakeWindow("w")
    focused = FakeTab("focused", window, sessions=[FakeSession()])
    window.tabs.append(focused)
    app = FakeApp([window])
    app.current_terminal_window.current_tab = focused

    found = asyncio.run(iterm_groups.find_tab_by_tty(app, "/dev/ttys009"))

    assert found is focused
