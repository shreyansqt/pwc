# PWCBar

A native macOS menu-bar app for PWC. It surfaces the durable task board, the live
worker sessions, and the coordinator hand-offs — all from the menu bar, no Dock
icon (`LSUIElement`). Built in Swift with AppKit (`NSStatusItem` + `NSMenu`).

**Core principle:** the app drives only the deterministic layer
(`scripts/taskdb.py`, `worker_status.py`) and **never reasons**. Anything needing
judgment is a button that launches a real Claude session in iTerm2 — the app
composes no seed and makes no decisions about a task.

## The menu

- **Coordinator** — *Find work…* and *Show work…* open an iTerm2 tab running
  `claude /pwc-find-work` / `/pwc-show-work` in the selected workspace.
- **Live workers** — the in-progress tasks whose worker session is still alive
  (via `worker_status.py`). Clicking one focuses that worker's iTerm2 tab. (Match
  is by the claude `--session-id` uuid → pid → tty → the iTerm2 session on that
  tty; no title or bookkeeping needed.)
- **Board** — every non-archived task from `taskdb.py summary`, grouped by status
  band (In progress / Blocked / Pending / Done), each sorted by priority then
  recency. Priority is a colored dot at the row edge; the workdir is shown dim.
  Each task's submenu offers *Start / resume…* (opens `claude /pwc-start-work
  <id>`) and, for a live worker, *Focus worker tab*.
- **Workspace** — the tracked workspaces (each a folder with a `.pwc/taskdb.db`);
  pick which one the board reads from, add/remove, refresh, quit.

The menu rebuilds from fresh data each time it opens — the board changes
out-of-band (workers, the coordinator), so reading on open is always correct.

## Scripts location

The app shells out to the PWC python CLIs. It resolves the scripts dir from
`$PWC_SCRIPTS` if set, else `~/work/side-projects/pwc/scripts` (this repo). The
bundle does not carry the scripts — they live in the repo and evolve there.

## Install

Download `PWCBar-<version>.dmg` from the repo's
[releases](https://github.com/shreyansqt/pwc/releases) (PWCBar releases are the
`pwcbar-v*` tags), open it, and drag PWCBar to Applications. The app is signed
with a Developer ID and notarized by Apple, so it launches without a Gatekeeper
warning.

Releases are cut by pushing a version tag (`git tag pwcbar-v1.0.0 && git push
origin pwcbar-v1.0.0`); CI builds, signs, notarizes, and attaches the DMG.

## Build

Requires Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen)
(`brew install xcodegen`).

```sh
cd app
xcodegen generate   # project.yml -> PWCBar.xcodeproj
xcodebuild -project PWCBar.xcodeproj -scheme PWCBar -configuration Debug \
  -derivedDataPath ./build build CODE_SIGNING_ALLOWED=NO
open build/Build/Products/Debug/PWCBar.app
```

The generated `PWCBar.xcodeproj` and `build/` are git-ignored; regenerate from
`project.yml`.

## State

App state lives under `~/Library/Application Support/PWCBar/`:

- `workspaces.json` — tracked workspace folders + the selected one
- `pwcbar.log` — the app's own diagnostic log (script/iTerm2 calls, errors)

## Scope (v1)

Read board + live workers + the iTerm2 hand-off buttons. Not yet wired: in-app
write quick-actions (archive / priority / status via `taskdb.py update-task` /
`archive`) and jump-to-ref opens (PR / Jira / Slack) — both are low-risk
deterministic calls left for a later cut.
