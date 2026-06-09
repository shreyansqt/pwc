import Foundation

/// One task row from `taskdb.py summary`. Mirrors that JSON exactly — the script
/// is the source of truth, so we decode its shape rather than impose our own.
struct PWCTask: Decodable, Identifiable {
    let id: String
    let type: String
    let title: String
    let status: String
    let priority: Int
    let parked: Int
    let parkedReason: String?
    let archivedAt: String?
    let workdir: String?
    let sessionID: String?
    let lastEventAt: String?

    enum CodingKeys: String, CodingKey {
        case id, type, title, status, priority, parked
        case parkedReason = "parked_reason"
        case archivedAt = "archived_at"
        case workdir
        case sessionID = "session_id"
        case lastEventAt = "last_event_at"
    }

    var isParked: Bool { parked != 0 }

    /// In-progress tasks carry a worker session id; only those are worth a
    /// liveness check.
    var isInProgress: Bool { status == "in-progress" }
}

/// Status bands the board groups by, in display order. `summary` excludes
/// archived rows already, so we never see an "archived" band here.
enum StatusBand: String, CaseIterable {
    case inProgress = "in-progress"
    case blocked
    case pending
    case done

    var title: String {
        switch self {
        case .inProgress: return "In progress"
        case .blocked: return "Blocked"
        case .pending: return "Pending"
        case .done: return "Done"
        }
    }

    /// Bands not matched by a known status fall through to this catch-all so a
    /// new status value never silently drops a task from the board.
    static func band(for status: String) -> StatusBand? {
        StatusBand(rawValue: status)
    }
}

/// One row from `worker_status.py`: is this session's worker process alive?
struct WorkerStatus: Decodable {
    let sessionID: String
    let alive: Bool
    let task: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case alive, task
    }
}

/// One reference on a task (`detail.refs`): a PR/Jira/Slack/url pointer, either an
/// identity ref (what the task IS) or a working ref (links gathered while working).
struct TaskRef: Decodable {
    let kind: String       // identity | working
    let refType: String    // jira | slack | url | github | …
    let value: String      // a URL, or a bare key like "SMT-973" for jira
    let label: String?

    enum CodingKeys: String, CodingKey {
        case kind, value, label
        case refType = "ref_type"
    }

    /// Whether `value` is already an openable URL (vs a bare key we must build one for).
    var isURL: Bool { value.hasPrefix("http://") || value.hasPrefix("https://") }
}

/// One event on a task's timeline (`detail.events`). Oldest-first as returned.
struct TaskEvent: Decodable {
    let at: String         // ISO8601 UTC
    let source: String
    let kind: String
    let detail: String?
}

/// The full per-task payload from `taskdb.py detail` — fields + refs + timeline.
/// We decode only what the drill-down menu needs; extra keys are ignored.
struct TaskDetail: Decodable {
    let task: PWCTask
    let refs: [TaskRef]
    let events: [TaskEvent]
}
