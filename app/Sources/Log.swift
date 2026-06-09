import Foundation

/// PWCBar's own diagnostic log — records what the app does internally (script
/// shell-outs, iTerm2 launches, errors) so misbehavior can be traced after the
/// fact. Writes to `~/Library/Application Support/PWCBar/pwcbar.log`, size-capped.
enum Log {
    private static let queue = DispatchQueue(label: "pwcbar.diaglog")
    private static let maxBytes: UInt64 = 2 * 1024 * 1024 // 2 MB, then rotate to .1

    /// Application Support dir for the app; created on first access.
    static let root: URL = {
        let dir = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PWCBar", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    private static var url: URL { root.appendingPathComponent("pwcbar.log") }

    enum Level: String { case info = "INFO", warn = "WARN", error = "ERROR" }

    static func info(_ message: @autoclosure () -> String) { write(.info, message()) }
    static func warn(_ message: @autoclosure () -> String) { write(.warn, message()) }
    static func error(_ message: @autoclosure () -> String) { write(.error, message()) }

    private static func write(_ level: Level, _ message: String) {
        let line = "\(timestamp()) [\(level.rawValue)] \(message)\n"
        queue.async {
            rotateIfNeeded()
            guard let data = line.data(using: .utf8) else { return }
            if let handle = try? FileHandle(forWritingTo: url) {
                handle.seekToEndOfFile()
                handle.write(data)
                try? handle.close()
            } else {
                try? data.write(to: url)
            }
        }
        #if DEBUG
        NSLog("PWCBar \(level.rawValue): \(message)")
        #endif
    }

    private static func timestamp() -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: Date())
    }

    private static func rotateIfNeeded() {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attrs[.size] as? UInt64, size > maxBytes else { return }
        let rotated = url.appendingPathExtension("1")
        try? FileManager.default.removeItem(at: rotated)
        try? FileManager.default.moveItem(at: url, to: rotated)
    }
}
