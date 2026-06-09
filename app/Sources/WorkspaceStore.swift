import Foundation

/// The workspace folders the user tracks. A PWC workspace is a folder holding a
/// `.pwc/taskdb.db` — the durable task database the scripts read/write. The app
/// runs every script with `--workspace <path>` against the selected workspace.
///
/// Persisted as a plain list of paths in Application Support, mirroring how
/// StackBar tracks its workspace folders. v1 keeps it deliberately small: a
/// list, a selected one, add/remove.
@MainActor
final class WorkspaceStore: ObservableObject {
    @Published private(set) var workspaces: [URL] = []
    /// The workspace the menu currently reads from. Defaults to the first.
    @Published var selected: URL?
    /// Per-workspace Jira base URL (e.g. https://smartasteuern.atlassian.net),
    /// keyed by workspace path. Used to turn a bare Jira key into a browse link.
    @Published private(set) var jiraBaseByPath: [String: String] = [:]

    private let storeURL = Log.root.appendingPathComponent("workspaces.json")

    private struct Persisted: Codable {
        var paths: [String]
        var selected: String?
        var jiraBaseByPath: [String: String]?
    }

    /// Jira base URL for the selected workspace, if set (trailing slash trimmed).
    var selectedJiraBase: String? {
        guard let path = selected?.path, let base = jiraBaseByPath[path], !base.isEmpty else {
            return nil
        }
        return base.hasSuffix("/") ? String(base.dropLast()) : base
    }

    func setJiraBase(_ base: String, for workspace: URL) {
        let path = workspace.standardizedFileURL.path
        let trimmed = base.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { jiraBaseByPath[path] = nil } else { jiraBaseByPath[path] = trimmed }
        save()
    }

    init() { load() }

    /// A folder is a valid PWC workspace iff it contains `.pwc/taskdb.db`.
    static func hasTaskDB(_ url: URL) -> Bool {
        FileManager.default.fileExists(
            atPath: url.appendingPathComponent(".pwc/taskdb.db").path)
    }

    func add(_ url: URL) {
        let std = url.standardizedFileURL
        guard !workspaces.contains(std) else {
            selected = std
            save()
            return
        }
        workspaces.append(std)
        if selected == nil { selected = std }
        save()
    }

    func remove(_ url: URL) {
        let std = url.standardizedFileURL
        workspaces.removeAll { $0 == std }
        if selected == std { selected = workspaces.first }
        save()
    }

    func select(_ url: URL) {
        guard workspaces.contains(url.standardizedFileURL) else { return }
        selected = url.standardizedFileURL
        save()
    }

    // MARK: - Persistence

    private func load() {
        guard let data = try? Data(contentsOf: storeURL),
              let decoded = try? JSONDecoder().decode(Persisted.self, from: data) else {
            return
        }
        workspaces = decoded.paths.map { URL(fileURLWithPath: $0).standardizedFileURL }
        jiraBaseByPath = decoded.jiraBaseByPath ?? [:]
        if let sel = decoded.selected {
            let selURL = URL(fileURLWithPath: sel).standardizedFileURL
            selected = workspaces.contains(selURL) ? selURL : workspaces.first
        } else {
            selected = workspaces.first
        }
    }

    private func save() {
        let persisted = Persisted(paths: workspaces.map(\.path), selected: selected?.path,
                                  jiraBaseByPath: jiraBaseByPath.isEmpty ? nil : jiraBaseByPath)
        guard let data = try? JSONEncoder().encode(persisted) else { return }
        try? data.write(to: storeURL)
    }
}
