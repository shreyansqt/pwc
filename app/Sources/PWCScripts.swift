import Foundation

/// Typed access to the PWC deterministic scripts. Every method here shells out to
/// a python CLI and decodes its JSON — the app reasons about NOTHING; it just
/// reads what the scripts report. Calls block, so run them off the main actor.
///
/// The scripts dir is resolved once: `$PWC_SCRIPTS` if set, else the known repo
/// location `~/work/side-projects/pwc/scripts`. (The app bundle doesn't carry the
/// scripts; they live in the repo and evolve there.)
enum PWCScripts {
    static let scriptsDir: URL = {
        if let override = ProcessInfo.processInfo.environment["PWC_SCRIPTS"], !override.isEmpty {
            return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
        }
        return URL(fileURLWithPath:
            (("~/work/side-projects/pwc/scripts") as NSString).expandingTildeInPath)
    }()

    static let python = "/usr/bin/python3"

    /// Python interpreter that has the `iterm2` module — required only by
    /// `iterm_open.py`. Apple's `/usr/bin/python3` does NOT ship iterm2, so the
    /// task scripts (stdlib-only) use `python`, but the tab opener needs a python
    /// where `import iterm2` works. Resolve once: `$PWC_PYTHON` if set, else the
    /// first common interpreter whose iterm2 imports.
    static let itermPython: String = {
        if let override = ProcessInfo.processInfo.environment["PWC_PYTHON"], !override.isEmpty {
            return (override as NSString).expandingTildeInPath
        }
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/opt/local/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            if case .success(let r) = Shell.run(path, ["-c", "import iterm2"]), r.ok {
                return path
            }
        }
        return "/usr/bin/python3"   // fall back; iterm_open.py will report the missing module
    }()

    private static func script(_ name: String) -> String {
        scriptsDir.appendingPathComponent(name).path
    }

    /// True if the scripts dir actually has the CLIs we need.
    static var available: Bool {
        FileManager.default.fileExists(atPath: script("taskdb.py"))
    }

    // MARK: - taskdb.py

    /// The board: every non-archived task on it, via `taskdb.py summary`.
    static func summary(workspace: URL) -> Result<[PWCTask], ShellError> {
        Shell.runJSON([PWCTask].self, python,
                      [script("taskdb.py"), "--workspace", workspace.path, "summary"])
    }

    /// Full per-task detail (fields + refs + events), via `taskdb.py detail`.
    static func detail(workspace: URL, task: String) -> Result<TaskDetail, ShellError> {
        Shell.runJSON(TaskDetail.self, python,
                      [script("taskdb.py"), "--workspace", workspace.path,
                       "detail", "--task", task])
    }

    // MARK: - taskdb.py (writes)

    /// Set a task's priority, via `update-task --priority`. Returns the raw result
    /// so the caller can surface a non-zero exit; the script emits JSON on success.
    static func setPriority(workspace: URL, task: String, priority: Int) -> Result<ShellResult, ShellError> {
        Shell.run(python, [script("taskdb.py"), "--workspace", workspace.path,
                           "update-task", "--task", task, "--priority", String(priority)])
    }

    /// Set a task's status AND log it on the timeline in one transaction, via
    /// `log-event --set-status`. The detail is tagged so the timeline shows the
    /// change came from the app, not a reasoning coordinator.
    static func setStatus(workspace: URL, task: String, status: String) -> Result<ShellResult, ShellError> {
        Shell.run(python, [script("taskdb.py"), "--workspace", workspace.path,
                           "log-event", "--task", task,
                           "--source", "coordinator", "--kind", "status",
                           "--set-status", status,
                           "--detail", "status -> \(status) (via PWCBar)"])
    }

    /// Archive a task off the board (preserves status), via `archive --reason`.
    static func archive(workspace: URL, task: String, reason: String) -> Result<ShellResult, ShellError> {
        Shell.run(python, [script("taskdb.py"), "--workspace", workspace.path,
                           "archive", "--task", task, "--reason", reason])
    }

    // MARK: - worker_status.py

    /// Liveness for a set of worker session ids, via `worker_status.py`. Empty in
    /// → empty out (no shell-out). The script needs no workspace.
    static func workerStatus(sessionIDs: [String]) -> Result<[WorkerStatus], ShellError> {
        let ids = sessionIDs.filter { !$0.isEmpty }
        guard !ids.isEmpty else { return .success([]) }
        return Shell.runJSON([WorkerStatus].self, python,
                             [script("worker_status.py"),
                              "--session-ids", ids.joined(separator: ",")])
    }

    // MARK: - iterm_ctl.py (iTerm2 control)

    /// Open `command` in a new iTerm2 tab with a stable, locked title, via
    /// `iterm_ctl.py open` (iTerm2 Python API `async_set_title` — the only reliable
    /// way to title a tab so the running program can't overwrite it). Uses the
    /// python that has the iterm2 module, not the stdlib one.
    static func openTab(command: String, title: String) -> Result<ShellResult, ShellError> {
        Shell.run(itermPython, [script("iterm_ctl.py"), "open",
                                "--command", command, "--title", title])
    }

    /// Focus the iTerm2 tab whose session has `tty`, via `iterm_ctl.py focus`. The
    /// app resolves a worker's session uuid → tty (stdlib pgrep/ps) and passes it
    /// here. Same Python-API path as openTab, so all iTerm2 control is unified.
    static func focusTTY(_ tty: String) -> Result<ShellResult, ShellError> {
        Shell.run(itermPython, [script("iterm_ctl.py"), "focus", "--tty", tty])
    }
}
