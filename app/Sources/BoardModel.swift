import Combine
import Foundation

/// Holds the latest board snapshot the menu renders. It does no reasoning: it
/// runs `taskdb.py summary`, then `worker_status.py` for the in-progress tasks'
/// sessions, and publishes the results. The menu rebuilds from this on each open.
///
/// Loading is async (off the main actor — the scripts block); `load()` is
/// fire-and-forget and flips `isLoading` so the menu can show a spinner row.
@MainActor
final class BoardModel: ObservableObject {
    @Published private(set) var tasks: [PWCTask] = []
    /// session_id → alive, for in-progress tasks. Absent key = not checked / no session.
    @Published private(set) var liveBySession: [String: Bool] = [:]
    @Published private(set) var isLoading = false
    /// Last error from a load, shown as a menu row so failures aren't silent.
    @Published private(set) var lastError: String?
    @Published private(set) var lastLoaded: Date?

    private let store: WorkspaceStore
    private var inFlight = false

    init(store: WorkspaceStore) {
        self.store = store
    }

    /// Tasks for the selected workspace, grouped into status bands in display
    /// order. Within a band, sorted by priority (1 = highest) then recency.
    var bands: [(band: StatusBand, tasks: [PWCTask])] {
        var grouped: [StatusBand: [PWCTask]] = [:]
        for task in tasks {
            guard let band = StatusBand.band(for: task.status) else { continue }
            grouped[band, default: []].append(task)
        }
        return StatusBand.allCases.compactMap { band in
            guard let rows = grouped[band], !rows.isEmpty else { return nil }
            let sorted = rows.sorted {
                if $0.priority != $1.priority { return $0.priority < $1.priority }
                return ($0.lastEventAt ?? "") > ($1.lastEventAt ?? "")
            }
            return (band, sorted)
        }
    }

    /// Kick off a refresh. No-ops if one is already running. Reads the selected
    /// workspace; if none is selected, clears the board.
    func load() {
        guard !inFlight else { return }
        guard let workspace = store.selected else {
            tasks = []; liveBySession = [:]; lastError = nil
            return
        }
        inFlight = true
        isLoading = true

        Task.detached { [weak self] in
            let snapshot = Self.fetch(workspace: workspace)
            await MainActor.run { [weak self] in
                guard let self else { return }
                self.tasks = snapshot.tasks
                self.liveBySession = snapshot.live
                self.lastError = snapshot.error
                self.lastLoaded = Date()
                self.isLoading = false
                self.inFlight = false
            }
        }
    }

    /// An immutable result of one board fetch — the tasks, the per-session
    /// liveness, and a fatal error (only set if `summary` itself failed).
    private struct Snapshot {
        let tasks: [PWCTask]
        let live: [String: Bool]
        let error: String?
    }

    /// Run the two scripts and return a snapshot. Blocking; runs off the main
    /// actor. A worker-status failure is logged but non-fatal (we still show the
    /// board); only a summary failure surfaces as `error`.
    private nonisolated static func fetch(workspace: URL) -> Snapshot {
        switch PWCScripts.summary(workspace: workspace) {
        case .failure(let e):
            Log.error("summary failed: \(e.localizedDescription)")
            return Snapshot(tasks: [], live: [:], error: e.localizedDescription)
        case .success(let rows):
            let sessions = rows.filter { $0.isInProgress }.compactMap { $0.sessionID }
            var live: [String: Bool] = [:]
            switch PWCScripts.workerStatus(sessionIDs: sessions) {
            case .success(let statuses):
                for s in statuses { live[s.sessionID] = s.alive }
            case .failure(let e):
                Log.warn("worker_status failed: \(e.localizedDescription)")
            }
            return Snapshot(tasks: rows, live: live, error: nil)
        }
    }
}
