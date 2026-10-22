#!/usr/bin/env python3
"""One iTerm2 tab group per PWC workspace.

A tab group is a named chip in the tab bar. Its members stay next to each
other; iTerm2 reorders tabs to keep that true. Groups do not span windows, so
a workspace's coordinator tab and worker tabs live in the window that already
holds the group.

The chip shows the workspace directory's name (`side-projects`, `smarta`).
The identity we search by is the absolute workspace path, stored on each
member tab as the user variable `user.pwc_workspace`. A renamed chip still
matches. A group created by hand, with the right name and no variable yet,
matches by name until a member is stamped.

Tab groups need iTerm2 3.7.4 or newer (Python API protocol 1.19, package
`iterm2>=2.24`). Older builds report unsupported. Callers keep going: a
missing group must not stop `pwc coord` or `pwc spawn`.
"""

from __future__ import annotations

import os
from pathlib import Path

# User variables must start with "user." or iTerm2 rejects them.
VARIABLE = "user.pwc_workspace"


def group_name(workspace_root: str) -> str:
    """The chip label: the workspace directory's basename."""
    return Path(workspace_root).resolve().name


def workspace_key(workspace_root: str) -> str:
    """Stable identity stored on member tabs. Survives a renamed chip."""
    return str(Path(workspace_root).resolve())


def same_window(a, b) -> bool:
    if a is None or b is None:
        return False
    if a is b:
        return True
    left = getattr(a, "window_id", None)
    right = getattr(b, "window_id", None)
    return bool(left) and left == right


def tab_groups_supported(connection) -> bool:
    """True when this iTerm2 can create and discover tab groups."""
    try:
        import iterm2
        return bool(iterm2.capabilities.supports_tab_groups(connection))
    except (ImportError, AttributeError):
        return False


def _windows(app):
    windows = getattr(app, "windows", None)
    if windows is None:
        windows = getattr(app, "terminal_windows", None)
    return list(windows or [])


def _group_of(tab):
    try:
        group = tab.tab_group
    except Exception:  # noqa: BLE001 — older iterm2 builds have no tab_group
        return None
    if group is None or not getattr(group, "group_id", None):
        return None
    return group


async def _tab_workspace(tab) -> str | None:
    try:
        value = await tab.async_get_variable(VARIABLE)
    except Exception:  # noqa: BLE001 — unset variable, or the API lacks it
        return None
    if isinstance(value, str) and value:
        return value
    return None


async def find_group(app, workspace_root: str):
    """Return `(window, group)` for this workspace, or None.

    A tab stamped with the workspace path wins over a same-named chip, so two
    workspaces that share a basename do not steal each other's group once
    either has been stamped.
    """
    key = workspace_key(workspace_root)
    name = group_name(workspace_root)
    named = None
    for window in _windows(app):
        for tab in window.tabs or []:
            group = _group_of(tab)
            if group is None:
                continue
            if await _tab_workspace(tab) == key:
                return window, group
            if group.name == name and named is None:
                named = (window, group)
    return named


async def window_for_workspace(app, workspace_root: str, fallback):
    """The window that already holds this workspace's group, else `fallback`."""
    found = await find_group(app, workspace_root)
    if found is None:
        return fallback
    return found[0]


async def create_background_tab(window):
    """Open a tab without taking focus when this iterm2 package allows it.

    `select=False` arrived with the tab-group API. The previous package raises
    TypeError on that keyword; the tab is still created, and the caller
    restores focus itself.
    """
    try:
        return await window.async_create_tab(select=False)
    except TypeError:
        return await window.async_create_tab()


async def _stamp(tab, workspace_root: str) -> None:
    try:
        await tab.async_set_variable(VARIABLE, workspace_key(workspace_root))
    except Exception:  # noqa: BLE001 — the group membership already landed
        pass


async def _place_in_window(app, tab, window):
    """Move `tab` into `window` when it is somewhere else. Return the live tab."""
    if same_window(getattr(tab, "window", None), window):
        return tab
    await window.async_set_tabs([tab])
    refresh = getattr(app, "async_refresh", None)
    if refresh is not None:
        await refresh()
    getter = getattr(app, "get_tab_by_id", None)
    if getter is None:
        return tab
    fresh = getter(tab.tab_id)
    return fresh or tab


async def ensure_member(app, tab, workspace_root: str, connection) -> str:
    """Put `tab` in the workspace's group.

    Returns `created`, `joined`, `already`, `unsupported`, or `no-tab`.
    Raises when the API is new enough and the call itself fails — the caller
    turns that into a warning and continues.
    """
    if not tab_groups_supported(connection):
        return "unsupported"
    found = await find_group(app, workspace_root)
    if found is None:
        window = getattr(tab, "window", None)
        if window is None:
            return "no-tab"
        await window.async_create_tab_group(group_name(workspace_root), [tab])
        await _stamp(tab, workspace_root)
        return "created"
    window, group = found
    tab = await _place_in_window(app, tab, window)
    current = _group_of(tab)
    if current is not None and current.group_id == group.group_id:
        await _stamp(tab, workspace_root)
        return "already"
    await tab.async_add_to_tab_group(group)
    await _stamp(tab, workspace_root)
    return "joined"


async def find_tab_by_tty(app, tty: str):
    """The tab whose session tty is `tty`.

    When no session reports a tty (shell integration off), the focused tab is
    the one the user just typed in. When sessions do report ttys and none
    match, return None rather than grouping whatever happens to be focused.
    """
    saw_tty = False
    for window in _windows(app):
        for tab in window.tabs or []:
            for session in getattr(tab, "sessions", None) or []:
                try:
                    value = await session.async_get_variable("tty")
                except Exception:  # noqa: BLE001
                    value = None
                if not value:
                    continue
                saw_tty = True
                if value == tty:
                    return tab
    if saw_tty:
        return None
    current = getattr(app, "current_terminal_window", None)
    if current is None:
        return None
    return getattr(current, "current_tab", None)


def group_tty(workspace_root: str, tty: str) -> str:
    """Join the current terminal's tab to the workspace group. Never raises."""
    try:
        import iterm2
    except ImportError:
        return "skipped: iterm2 module not installed"

    outcome = {"status": "skipped"}

    async def _main(connection):
        if not tab_groups_supported(connection):
            outcome["status"] = "unsupported"
            return
        app = await iterm2.async_get_app(connection)
        tab = await find_tab_by_tty(app, tty)
        if tab is None:
            outcome["status"] = "no-tab"
            return
        outcome["status"] = await ensure_member(app, tab, workspace_root, connection)

    try:
        iterm2.run_until_complete(_main)
    except Exception as exc:  # noqa: BLE001 — grouping is cosmetic
        return f"skipped: {type(exc).__name__}: {exc}"
    return outcome["status"]


def current_tty() -> str | None:
    try:
        if not os.isatty(1):
            return None
        return os.ttyname(1)
    except OSError:
        return None
