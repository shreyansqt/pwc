import AppKit
import Foundation

/// Drives iTerm2 for the two things the app hands off to a terminal:
///   1. focus an existing worker's tab (given its claude `--session-id` uuid), and
///   2. open a NEW tab running a `claude` command (the AI hand-off buttons).
///
/// Focus is keyed off the uuid, NOT a tab title: the title is whatever the
/// running tool last set (often unreliable). Instead we map uuid → pid (`pgrep`)
/// → tty (`ps`) → the iTerm2 session whose `tty` matches, then activate it. This
/// is the same liveness signal `worker_status.py` uses, extended one hop to the
/// tty, so it stays deterministic and needs no session→tab bookkeeping.
///
/// All iTerm2 control is AppleScript via `osascript`, so the app carries no
/// python `iterm2` dependency of its own (spawn.py keeps using the Python API for
/// its own richer placement logic; this app only needs activate + open-tab).
enum ITerm {
    /// Focus the iTerm2 tab whose claude process carries `sessionID`. Returns
    /// false (with a logged reason) if the worker isn't found or iTerm2 can't be
    /// driven — the caller surfaces that. Blocks; call off the main actor.
    @discardableResult
    static func focusWorker(sessionID: String) -> Bool {
        guard let tty = ttyForSession(sessionID) else {
            Log.warn("focusWorker: no live process for session \(sessionID)")
            return false
        }
        // iTerm2 reports tty as a full device path (/dev/ttys003); `ps` gives the
        // short form (ttys003). Match on the suffix.
        let script = """
        tell application "iTerm2"
            activate
            repeat with w in windows
                repeat with t in tabs of w
                    repeat with s in sessions of t
                        if (tty of s) ends with "\(tty)" then
                            select w
                            select t
                            select s
                            return "ok"
                        end if
                    end repeat
                end repeat
            end repeat
            return "notfound"
        end tell
        """
        let result = runOSAScript(script)
        switch result {
        case .success(let out) where out.trimmingCharacters(in: .whitespacesAndNewlines) == "ok":
            return true
        case .success(let out):
            Log.warn("focusWorker: iTerm2 returned '\(out.trimmingCharacters(in: .whitespacesAndNewlines))' for tty \(tty)")
            return false
        case .failure(let e):
            Log.error("focusWorker: osascript failed: \(e.localizedDescription)")
            return false
        }
    }

    // Tab launches (the Find work / Show work / Start hand-offs) go through
    // `iterm_open.py` (PWCScripts.openTab) — the iTerm2 Python API's
    // async_set_title is the only reliable way to give a tab a stable title that
    // the running program can't overwrite. AppleScript stays here only for
    // focusing an existing worker's tab, which doesn't need a title set.

    // MARK: - uuid → tty

    /// The short tty name (e.g. "ttys003") of the running process whose argv
    /// carries `sessionID`, or nil if none. uuid → pid (`pgrep -f`) → tty (`ps`).
    private static func ttyForSession(_ sessionID: String) -> String? {
        guard !sessionID.isEmpty else { return nil }
        guard case .success(let pgrep) = Shell.run("/usr/bin/pgrep", ["-f", sessionID]),
              pgrep.ok else { return nil }
        // Multiple pids can match (claude plus children); the first with a real
        // tty is the interactive session. Try each.
        let pids = pgrep.stdout.split(whereSeparator: \.isNewline).map(String.init)
        for pid in pids {
            guard case .success(let ps) = Shell.run("/bin/ps", ["-o", "tty=", "-p", pid]),
                  ps.ok else { continue }
            let tty = ps.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
            if !tty.isEmpty && tty != "??" { return tty }
        }
        return nil
    }

    // MARK: - osascript

    private static func runOSAScript(_ source: String) -> Result<String, ShellError> {
        switch Shell.run("/usr/bin/osascript", ["-e", source]) {
        case .failure(let e):
            return .failure(e)
        case .success(let r):
            guard r.ok else { return .failure(.nonZeroExit(code: r.exitCode, stderr: r.stderr)) }
            return .success(r.stdout)
        }
    }
}
