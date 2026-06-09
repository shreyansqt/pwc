import Foundation

/// A finished subprocess: exit code plus captured stdout/stderr.
struct ShellResult {
    let exitCode: Int32
    let stdout: String
    let stderr: String

    var ok: Bool { exitCode == 0 }
}

enum ShellError: Error, LocalizedError {
    case launchFailed(String)
    case nonZeroExit(code: Int32, stderr: String)
    case decodeFailed(String)

    var errorDescription: String? {
        switch self {
        case .launchFailed(let m): return "Failed to launch: \(m)"
        case .nonZeroExit(let code, let stderr):
            return "Exited \(code): \(stderr.trimmingCharacters(in: .whitespacesAndNewlines))"
        case .decodeFailed(let m): return "Could not decode output: \(m)"
        }
    }
}

/// Thin synchronous wrapper around `Process`. The PWC scripts are short-lived
/// CLIs that print one JSON value on stdout and exit — so a blocking run with
/// captured pipes is the whole story (no streaming, no long-lived children, none
/// of the process-lifecycle machinery a dev-server runner needs). Callers hop
/// this onto a background queue; never call it on the main actor.
enum Shell {
    /// Run `executable args…` in `directory`, blocking until it exits. Captures
    /// stdout and stderr fully. Returns the result even on a non-zero exit — the
    /// caller decides whether that's fatal.
    static func run(_ executable: String, _ args: [String],
                    in directory: URL? = nil,
                    extraEnv: [String: String] = [:]) -> Result<ShellResult, ShellError> {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: executable)
        proc.arguments = args
        if let directory { proc.currentDirectoryURL = directory }
        if !extraEnv.isEmpty {
            var env = ProcessInfo.processInfo.environment
            extraEnv.forEach { env[$0.key] = $0.value }
            proc.environment = env
        }

        let outPipe = Pipe(), errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe

        do {
            try proc.run()
        } catch {
            return .failure(.launchFailed(error.localizedDescription))
        }
        // Read both pipes fully before waiting, so a large output can't deadlock
        // on a full pipe buffer while we wait for exit.
        let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
        let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()

        return .success(ShellResult(
            exitCode: proc.terminationStatus,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? ""
        ))
    }

    /// Run a command that emits one JSON value on stdout and decode it. Maps a
    /// non-zero exit or a decode failure to a `ShellError`.
    static func runJSON<T: Decodable>(_ type: T.Type,
                                      _ executable: String, _ args: [String],
                                      in directory: URL? = nil) -> Result<T, ShellError> {
        switch run(executable, args, in: directory) {
        case .failure(let e):
            return .failure(e)
        case .success(let r):
            guard r.ok else {
                return .failure(.nonZeroExit(code: r.exitCode, stderr: r.stderr))
            }
            guard let data = r.stdout.data(using: .utf8) else {
                return .failure(.decodeFailed("stdout was not UTF-8"))
            }
            do {
                return .success(try JSONDecoder().decode(T.self, from: data))
            } catch {
                return .failure(.decodeFailed(error.localizedDescription))
            }
        }
    }
}
