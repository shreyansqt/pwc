import AppKit
import Foundation

/// Focuses an existing worker's iTerm2 tab. The actual iTerm2 driving is done by
/// `iterm_ctl.py` (the Python API) — this type only resolves the worker's claude
/// `--session-id` uuid to a tty, then hands that tty to the helper.
///
/// uuid → tty is pure stdlib (`pgrep` + `ps`): the same liveness signal
/// `worker_status.py` uses, extended one hop to the tty. Keeping it in Swift means
/// the cheap part needs no python; only the iTerm2 activation goes through the
/// helper. This is also the one place that used AppleScript — now removed, so all
/// iTerm2 control flows through a single Python-API path.
enum ITerm {
    /// Focus the iTerm2 tab whose claude process carries `sessionID`. Returns
    /// false (with a logged reason) if the worker isn't found or iTerm2 can't be
    /// driven. Blocks; call off the main actor.
    @discardableResult
    static func focusWorker(sessionID: String) -> Bool {
        guard let tty = ttyForSession(sessionID) else {
            Log.warn("focusWorker: no live process for session \(sessionID)")
            return false
        }
        switch PWCScripts.focusTTY(tty) {
        case .success(let r) where r.ok:
            // The helper emits {"focused": true/false}; treat a non-focused result
            // (tty not found among iTerm tabs) as a soft failure.
            if r.stdout.contains("\"focused\": true") { return true }
            Log.warn("focusWorker: no iTerm2 tab for tty \(tty)")
            return false
        case .success(let r):
            Log.error("focusWorker: iterm_ctl failed (\(r.exitCode)): \(r.stderr)")
            return false
        case .failure(let e):
            Log.error("focusWorker: iterm_ctl failed: \(e.localizedDescription)")
            return false
        }
    }

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
}
