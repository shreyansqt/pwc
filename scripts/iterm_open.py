#!/usr/bin/env python3
"""Open a command in a new iTerm2 tab with a STABLE tab title.

A thin launch helper for the PWCBar menu-bar app's hand-off buttons (Find work /
Show work / Start task). It opens a new tab in the current iTerm2 window, sets the
tab title via the iTerm2 Python API's `async_set_title` — which *locks* the title
so the running program can't overwrite it — then types the command into the tab's
shell.

This exists because AppleScript can't set a sticky tab title: the default iTerm2
profile syncs the tab title from the running program, so an AppleScript-set name
is clobbered immediately. `async_set_title` (the same call spawn.py uses for worker
tabs) is the only reliable way, and it needs the Python API. The app shells out to
this script just like it does taskdb.py / worker_status.py.

Unlike spawn.py (which opens worker tabs in the BACKGROUND and restores focus),
this focuses the new tab — the user clicked a button to start something and
expects to land in it.

Requires iTerm2 running with the Python API enabled
(Preferences -> General -> Magic -> Enable Python API) and `pip install iterm2`.
Fails with a clear message (never hangs) if it can't connect.

Usage:
  iterm_open.py --command <shell-command> --title <tab-title>
  iterm_open.py --command - --title <tab-title>     # read command from stdin
"""

from __future__ import annotations

import argparse
import sys

from _common import emit, fail


def open_tab(*, command: str, title: str | None):
    """Open `command` in a new iTerm2 tab titled `title`. Returns placement dict."""
    try:
        import iterm2  # lazy: importing this module shouldn't require iterm2
    except ImportError:
        fail("iterm2 module not installed — run `pip install iterm2`")

    placement: dict = {}

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        window = app.current_terminal_window
        if window is None:
            # No window open — create one rather than failing.
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
                # The sticky-title call: locks the tab title against the program.
                await tab.async_set_title(title)
            except Exception:  # noqa: BLE001 — title is cosmetic, never fatal
                pass

        if session is None:
            fail("tab has no session to run the command in")

        await session.async_send_text(command + "\r")
        placement["launched"] = True

    try:
        iterm2.run_until_complete(_main)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        fail(
            "could not reach iTerm2's Python API "
            "(is iTerm2 running with the API enabled in "
            f"Preferences -> General -> Magic?): {type(e).__name__}: {e}"
        )
    return placement


def main(argv=None):
    p = argparse.ArgumentParser(prog="iterm_open.py", description=__doc__)
    p.add_argument("--command", required=True,
                   help="shell command to run in the new tab, or '-' to read from stdin")
    p.add_argument("--title", help="tab title (locked via async_set_title)")
    args = p.parse_args(argv)

    command = sys.stdin.read().rstrip("\n") if args.command == "-" else args.command
    if not command.strip():
        fail("--command is empty")

    placement = open_tab(command=command, title=args.title)
    emit({"command": command, "title": args.title, **placement})


if __name__ == "__main__":
    main(sys.argv[1:])
