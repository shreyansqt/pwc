#!/usr/bin/env python3
"""Drive iTerm2 for the PWCBar menu-bar app — open a titled tab, or focus a tab.

The app hands off two iTerm2 operations to this helper (both via the iTerm2
Python API, so there's a single, reliable mechanism and no AppleScript):

  open   — open a new tab in the current window, set a STABLE tab title via
           `async_set_title` (which the running program can't overwrite — the
           reason AppleScript was unusable here), then run a command in it.
           Focuses the new tab (the user clicked a button to start something).

  focus  — find the tab whose session has a given tty (e.g. /dev/ttys003) and
           activate it. The app maps a worker's claude `--session-id` → pid → tty,
           then asks us to focus that tty's tab.

Requires iTerm2 running with the Python API enabled
(Preferences -> General -> Magic -> Enable Python API) and `pip install iterm2`.
Fails with a clear message (never hangs) if it can't connect.

Usage:
  iterm_ctl.py open  --command <shell-command|-> [--title <tab-title>]
  iterm_ctl.py focus --tty <tty>        # tty as /dev/ttysNNN or ttysNNN
"""

from __future__ import annotations

import argparse
import sys

from _common import emit, fail


def _import_iterm2():
    try:
        import iterm2  # lazy: importing this module shouldn't require iterm2
    except ImportError:
        fail("iterm2 module not installed — run `pip install iterm2`")
    return iterm2


def _run(iterm2, coro_factory):
    """Run an async (connection)->... against iTerm2, with a clear failure."""
    try:
        iterm2.run_until_complete(coro_factory)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        fail(
            "could not reach iTerm2's Python API "
            "(is iTerm2 running with the API enabled in "
            f"Preferences -> General -> Magic?): {type(e).__name__}: {e}"
        )


def open_tab(*, command: str, title: str | None):
    """Open `command` in a new iTerm2 tab titled `title`. Returns placement dict."""
    iterm2 = _import_iterm2()
    placement: dict = {}

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        window = app.current_terminal_window
        if window is None:
            window = await iterm2.Window.async_create(connection)
            if window is None:
                fail("no iTerm2 window and could not create one")
            tab = window.current_tab
        else:
            tab = await window.async_create_tab()
        if tab is None:
            fail("could not create an iTerm2 tab")

        session = tab.current_session
        placement["iterm_session_id"] = session.session_id if session else None
        if title:
            try:
                await tab.async_set_title(title)  # locks the tab title
            except Exception:  # noqa: BLE001 — title is cosmetic, never fatal
                pass
        if session is None:
            fail("tab has no session to run the command in")
        await session.async_send_text(command + "\r")
        placement["launched"] = True

    _run(iterm2, _main)
    return placement


def focus_tty(*, tty: str):
    """Activate the tab whose session has `tty`. Returns {focused: bool}."""
    iterm2 = _import_iterm2()
    # Match on the short suffix so /dev/ttys003 and ttys003 both work. Guard the
    # empty case explicitly: an empty needle would `endswith`-match every session.
    needle = tty.strip()
    if not needle:
        fail("--tty is empty")
    result: dict = {"focused": False}

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    stty = await session.async_get_variable("tty") or ""
                    if stty == needle or stty.endswith(needle):
                        await window.async_activate()
                        await tab.async_select()
                        await session.async_activate()
                        # Bring iTerm2 itself to the front.
                        await app.async_activate(raise_all_windows=False)
                        result["focused"] = True
                        result["tty"] = stty
                        return

    _run(iterm2, _main)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(prog="iterm_ctl.py", description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)

    po = sub.add_parser("open", help="open a titled tab and run a command")
    po.add_argument("--command", required=True,
                    help="shell command to run, or '-' to read from stdin")
    po.add_argument("--title", help="tab title (locked via async_set_title)")

    pf = sub.add_parser("focus", help="focus the tab whose session has a given tty")
    pf.add_argument("--tty", required=True, help="tty (/dev/ttysNNN or ttysNNN)")

    args = p.parse_args(argv)

    if args.action == "open":
        command = sys.stdin.read().rstrip("\n") if args.command == "-" else args.command
        if not command.strip():
            fail("--command is empty")
        placement = open_tab(command=command, title=args.title)
        emit({"command": command, "title": args.title, **placement})
    elif args.action == "focus":
        emit(focus_tty(tty=args.tty))


if __name__ == "__main__":
    main(sys.argv[1:])
