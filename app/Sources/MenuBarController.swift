import AppKit
import Combine
import Foundation

/// Owns the menu bar status item and its NSMenu. The menu is rebuilt from the
/// BoardModel each time it opens (NSMenuDelegate), so it always reflects current
/// data — the board changes out-of-band (workers, the coordinator), so reading on
/// open is simpler and always correct than maintaining live UI state.
///
/// This controller wires buttons to deterministic script calls and iTerm2
/// hand-offs. It never reasons about a task.
@MainActor
final class MenuBarController: NSObject, NSMenuDelegate {
    private let store: WorkspaceStore
    private let board: BoardModel
    private let statusItem: NSStatusItem
    private var cancellables: Set<AnyCancellable> = []

    init(store: WorkspaceStore, board: BoardModel) {
        self.store = store
        self.board = board
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()

        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        updateIcon()

        // When the board finishes loading while the menu is open, refresh it in
        // place so the spinner row becomes real content.
        board.objectWillChange
            .receive(on: RunLoop.main)
            .sink { [weak self] in
                DispatchQueue.main.async {
                    self?.updateIcon()
                    if let menu = self?.statusItem.menu, menu.highlightedItem != nil || self?.menuIsOpen == true {
                        menu.update()
                    }
                }
            }
            .store(in: &cancellables)
    }

    private var menuIsOpen = false

    // MARK: - Status bar icon

    private func updateIcon() {
        guard let button = statusItem.button else { return }
        let image = NSImage(systemSymbolName: "checklist", accessibilityDescription: "PWCBar")
        image?.isTemplate = true
        button.image = image
        button.imagePosition = .imageOnly
    }

    // MARK: - NSMenuDelegate

    func menuWillOpen(_ menu: NSMenu) {
        menuIsOpen = true
        // Always pull fresh data when the menu opens.
        board.load()
    }

    func menuDidClose(_ menu: NSMenu) { menuIsOpen = false }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()

        header(menu, "PWC", symbol: "checklist")
        menu.addItem(.separator())

        guard PWCScripts.available else {
            disabledRow(menu, "Scripts not found at \(PWCScripts.scriptsDir.path)")
            footer(menu)
            return
        }

        if store.workspaces.isEmpty {
            disabledRow(menu, "No workspace yet — add a folder with a .pwc/taskdb.db")
            addItem(menu, "Add Workspace…", #selector(addWorkspace), symbol: "folder.badge.plus")
            footer(menu)
            return
        }

        guard store.selected != nil else {
            disabledRow(menu, "No workspace selected")
            workspaceFooter(menu)
            return
        }

        // AI hand-off buttons — always available; they launch Claude sessions.
        sectionHeader(menu, "Coordinator")
        addItem(menu, "Find work…", #selector(findWork), symbol: "sparkle.magnifyingglass")
        addItem(menu, "Show work…", #selector(showWork), symbol: "sparkles")
        menu.addItem(.separator())

        // The board, grouped by status band. Live workers are marked inline (a
        // green dot on the in-progress row) rather than in a separate section —
        // they were duplicating the In progress band one-to-one.
        boardSection(menu)

        footer(menu)
    }

    // MARK: - Sections

    private func boardSection(_ menu: NSMenu) {
        if let err = board.lastError {
            sectionHeader(menu, "Board")
            disabledRow(menu, "Error: \(err)")
            menu.addItem(.separator())
            return
        }
        let bands = board.bands
        if bands.isEmpty {
            sectionHeader(menu, "Board")
            disabledRow(menu, board.isLoading ? "Loading…" : "No tasks on the board")
            menu.addItem(.separator())
            return
        }
        for (band, tasks) in bands {
            sectionHeader(menu, band.title)
            for task in tasks {
                menu.addItem(taskItem(task))
            }
        }
        menu.addItem(.separator())
    }

    /// Known status values offered in the Status submenu, in board order.
    private static let statuses = ["pending", "in-progress", "blocked", "awaiting-review", "done"]

    /// A board task row: priority cue + title + dir. Its submenu is a per-task
    /// briefing (Latest + References, lazily fetched from `detail`) plus the
    /// deterministic actions (Start/Focus/Priority/Status/Archive). The submenu is
    /// owned by a TaskSubmenuController that rebuilds it on open — so the heavy
    /// `detail` fetch happens only when you actually hover into a task, not for
    /// every row on every board open.
    /// Whether this task's worker session is currently alive.
    func isLive(_ task: PWCTask) -> Bool {
        task.isInProgress && (task.sessionID.map { board.liveBySession[$0] == true } ?? false)
    }

    /// "Focus worker tab" item for a live task, or nil if it has no live session.
    /// Placed at the TOP of the submenu so focusing a worker is always the first,
    /// consistently-located action — one hover, one click.
    func makeFocusItem(for task: PWCTask) -> NSMenuItem? {
        guard isLive(task), let sid = task.sessionID else { return nil }
        let focus = NSMenuItem(title: "Focus worker tab", action: #selector(focusWorker(_:)), keyEquivalent: "")
        focus.target = self
        focus.representedObject = sid
        focus.image = templateSymbol("macwindow")
        return focus
    }

    private func taskItem(_ task: PWCTask) -> NSMenuItem {
        let item = NSMenuItem(title: task.id, action: nil, keyEquivalent: "")
        item.attributedTitle = taskTitle(task, live: isLive(task))
        item.image = priorityDot(task.priority)
        if isLive(task) { item.toolTip = "Worker running — open to focus its tab" }

        let submenu = NSMenu()
        let controller = TaskSubmenuController(task: task, parent: self)
        submenu.delegate = controller
        objc_setAssociatedObject(submenu, &Self.submenuControllerKey, controller, .OBJC_ASSOCIATION_RETAIN)
        // Seed with one row so the submenu arrow shows before first open.
        submenu.addItem({ let i = NSMenuItem(title: task.title, action: nil, keyEquivalent: ""); i.isEnabled = false; return i }())
        item.submenu = submenu
        return item
    }

    private static var submenuControllerKey: UInt8 = 0

    /// Build the live worker/priority/status/archive actions for a task into its
    /// submenu — called by the submenu controller after the briefing rows. Detail
    /// is passed when available (for the current-priority/status checkmarks we use
    /// the task fields, which are enough).
    func appendTaskActions(to submenu: NSMenu, task: PWCTask) {
        let start = NSMenuItem(title: "Start / resume…", action: #selector(startTask(_:)), keyEquivalent: "")
        start.target = self
        start.representedObject = task.id
        start.image = templateSymbol("play.fill")
        submenu.addItem(start)

        submenu.addItem(.separator())

        // Priority submenu (1–4), current checked. Immediate write.
        let priority = NSMenuItem(title: "Priority", action: nil, keyEquivalent: "")
        priority.image = templateSymbol("flag")
        let pMenu = NSMenu()
        for p in 1...4 {
            let row = NSMenuItem(title: "p\(p)", action: #selector(setPriority(_:)), keyEquivalent: "")
            row.target = self
            row.representedObject = TaskValue(task: task.id, value: String(p))
            row.image = priorityDot(p)
            row.state = (task.priority == p) ? .on : .off
            pMenu.addItem(row)
        }
        priority.submenu = pMenu
        submenu.addItem(priority)

        // Status submenu, current checked. Immediate write (logs an event).
        let status = NSMenuItem(title: "Status", action: nil, keyEquivalent: "")
        status.image = templateSymbol("circle.dashed")
        let sMenu = NSMenu()
        for s in Self.statuses {
            let row = NSMenuItem(title: s, action: #selector(setStatus(_:)), keyEquivalent: "")
            row.target = self
            row.representedObject = TaskValue(task: task.id, value: s)
            row.state = (task.status == s) ? .on : .off
            sMenu.addItem(row)
        }
        status.submenu = sMenu
        submenu.addItem(status)

        // Archive — confirms + prompts for a reason.
        let archive = NSMenuItem(title: "Archive…", action: #selector(archiveTask(_:)), keyEquivalent: "")
        archive.target = self
        archive.representedObject = task.id
        archive.image = templateSymbol("archivebox")
        submenu.addItem(archive)
    }

    /// Open a task ref (PR/Slack/url, or a Jira key built into a browse URL).
    func openRef(_ ref: TaskRef) {
        let urlString: String
        if ref.isURL {
            urlString = ref.value
        } else if ref.refType == "jira", let base = store.selectedJiraBase {
            urlString = "\(base)/browse/\(ref.value)"
        } else {
            Log.warn("openRef: no openable URL for \(ref.refType) '\(ref.value)'")
            Self.alertOnMain("Can’t open this reference", refType: ref.refType, store: store)
            return
        }
        guard let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }

    /// Fetch a task's detail off the main actor, then call back on main.
    func loadDetail(task: String, then completion: @escaping (TaskDetail?) -> Void) {
        guard let workspace = store.selected else { completion(nil); return }
        Task.detached {
            let result = PWCScripts.detail(workspace: workspace, task: task)
            await MainActor.run {
                switch result {
                case .success(let d): completion(d)
                case .failure(let e):
                    Log.warn("detail(\(task)) failed: \(e.localizedDescription)")
                    completion(nil)
                }
            }
        }
    }

    private func footer(_ menu: NSMenu) {
        workspaceFooter(menu)
    }

    private func workspaceFooter(_ menu: NSMenu) {
        sectionHeader(menu, "Workspace")
        // One row per workspace; the selected one is checked. Submenu = Remove.
        for ws in store.workspaces {
            let item = NSMenuItem(title: ws.lastPathComponent, action: #selector(selectWorkspace(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = ws
            item.image = templateSymbol(ws == store.selected ? "largecircle.fill.circle" : "circle")
            item.toolTip = ws.path

            let sub = NSMenu()
            let jira = NSMenuItem(title: "Set Jira base URL…", action: #selector(setJiraBase(_:)), keyEquivalent: "")
            jira.target = self
            jira.representedObject = ws
            jira.image = templateSymbol("link")
            if let base = store.jiraBaseByPath[ws.path], !base.isEmpty { jira.toolTip = base }
            sub.addItem(jira)
            sub.addItem(.separator())
            let remove = NSMenuItem(title: "Remove", action: #selector(removeWorkspace(_:)), keyEquivalent: "")
            remove.target = self
            remove.representedObject = ws
            remove.image = templateSymbol("trash")
            sub.addItem(remove)
            item.submenu = sub

            menu.addItem(item)
        }
        addItem(menu, "Add Workspace…", #selector(addWorkspace), symbol: "folder.badge.plus")
        addItem(menu, "Refresh", #selector(refresh), symbol: "arrow.clockwise")
        menu.addItem(.separator())
        // Login item toggle. Use an image (not the system check state) so the
        // indicator sits in the same icon gutter as the items above — the
        // state-mark column renders misaligned when siblings carry images.
        let loginEnabled = LaunchAtLogin.isEnabled
        addItem(menu, "Open at Login", #selector(toggleLaunchAtLogin),
                symbol: loginEnabled ? "checkmark.circle.fill" : "circle")
        addItem(menu, "Quit PWCBar", #selector(quit), symbol: "power", key: "q")
    }

    @objc private func toggleLaunchAtLogin() {
        do {
            try LaunchAtLogin.setEnabled(!LaunchAtLogin.isEnabled)
        } catch {
            Self.alertOnMain("Couldn’t change login item",
                "\(error.localizedDescription)\n\nYou can manage PWCBar under System Settings → General → Login Items.")
        }
    }

    // MARK: - Actions: AI hand-offs

    // The coordinator commands run in a tab titled "PWC Coordinator"; starting a
    // task gets a tab titled after the task id.
    @objc private func findWork() { launchClaude(slashCommand: "/pwc-find-work", title: "PWC Coordinator") }
    @objc private func showWork() { launchClaude(slashCommand: "/pwc-show-work", title: "PWC Coordinator") }

    @objc private func startTask(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        launchClaude(slashCommand: "/pwc-start-work \(id)", title: id)
    }

    /// Open an iTerm2 tab in the selected workspace running `claude "<slash>"`,
    /// with a stable tab title. Goes through `iterm_open.py` (iTerm2 Python API
    /// async_set_title) so the title sticks — AppleScript can't lock a tab title.
    /// The app composes NO seed — it just hands the slash command to a real Claude
    /// session, which then does the reasoning.
    private func launchClaude(slashCommand: String, title: String) {
        guard let workspace = store.selected else { return }
        let command = "cd \(shellQuote(workspace.path)) && claude \(shellQuote(slashCommand))"
        runOffMain {
            switch PWCScripts.openTab(command: command, title: title) {
            case .success(let r) where r.ok:
                break
            case .success(let r):
                Self.alertOnMain("Couldn’t open an iTerm2 tab.",
                    r.stderr.isEmpty ? "Exit \(r.exitCode)" : r.stderr)
            case .failure(let e):
                Self.alertOnMain("Couldn’t open an iTerm2 tab.", e.localizedDescription)
            }
        }
    }

    // MARK: - Actions: deterministic

    @objc private func focusWorker(_ sender: NSMenuItem) {
        guard let sid = sender.representedObject as? String else { return }
        runOffMain {
            let ok = ITerm.focusWorker(sessionID: sid)
            if !ok { Self.alertOnMain("Couldn’t focus the worker tab.", "The worker may have exited, or iTerm2 isn’t reachable.") }
        }
    }

    @objc private func refresh() { board.load() }

    // MARK: - Actions: deterministic writes

    @objc private func setPriority(_ sender: NSMenuItem) {
        guard let tv = sender.representedObject as? TaskValue,
              let p = Int(tv.value), let workspace = store.selected else { return }
        runWrite("Set priority") { PWCScripts.setPriority(workspace: workspace, task: tv.task, priority: p) }
    }

    @objc private func setStatus(_ sender: NSMenuItem) {
        guard let tv = sender.representedObject as? TaskValue, let workspace = store.selected else { return }
        runWrite("Set status") { PWCScripts.setStatus(workspace: workspace, task: tv.task, status: tv.value) }
    }

    @objc private func archiveTask(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String, let workspace = store.selected else { return }
        NSApp.activate(ignoringOtherApps: true)
        // Confirm + collect a reason (archive requires one).
        let alert = NSAlert()
        alert.messageText = "Archive \(id)?"
        alert.informativeText = "Removes it from the board without marking it done (status is preserved). Give a short reason."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Archive")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
        field.placeholderString = "why it’s leaving the board"
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let reason = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !reason.isEmpty else {
            Self.alertOnMain("Archive needs a reason.", "Nothing was changed.")
            return
        }
        runWrite("Archive") { PWCScripts.archive(workspace: workspace, task: id, reason: reason) }
    }

    /// Run a deterministic write off the main actor; on failure show the script's
    /// stderr, and always refresh the board so the menu reflects the new state.
    private func runWrite(_ label: String, _ work: @escaping () -> Result<ShellResult, ShellError>) {
        runOffMain {
            switch work() {
            case .success(let r) where r.ok:
                Log.info("\(label): ok")
            case .success(let r):
                Self.alertOnMain("\(label) failed.", r.stderr.isEmpty ? "Exit \(r.exitCode)" : r.stderr)
            case .failure(let e):
                Self.alertOnMain("\(label) failed.", e.localizedDescription)
            }
            DispatchQueue.main.async { [weak self] in self?.board.load() }
        }
    }

    @objc private func selectWorkspace(_ sender: NSMenuItem) {
        guard let ws = sender.representedObject as? URL else { return }
        store.select(ws)
        board.load()
    }

    @objc private func addWorkspace() {
        NSApp.activate(ignoringOtherApps: true)
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Add Workspace"
        panel.message = "Choose a folder containing a .pwc/taskdb.db"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard WorkspaceStore.hasTaskDB(url) else {
            let alert = NSAlert()
            alert.messageText = "No PWC task database here"
            alert.informativeText = "\(url.path)\n\nA PWC workspace must contain a .pwc/taskdb.db file."
            alert.alertStyle = .warning
            alert.runModal()
            return
        }
        store.add(url)
        board.load()
    }

    @objc private func removeWorkspace(_ sender: NSMenuItem) {
        guard let ws = sender.representedObject as? URL else { return }
        store.remove(ws)
        board.load()
    }

    @objc private func setJiraBase(_ sender: NSMenuItem) {
        guard let ws = sender.representedObject as? URL else { return }
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Jira base URL for \(ws.lastPathComponent)"
        alert.informativeText = "Used to open a bare Jira key (e.g. SMT-973) as <base>/browse/SMT-973."
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        field.placeholderString = "https://your-org.atlassian.net"
        field.stringValue = store.jiraBaseByPath[ws.path] ?? ""
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        store.setJiraBase(field.stringValue, for: ws)
    }

    @objc private func quit() { NSApp.terminate(nil) }

    // MARK: - Helpers

    /// Run blocking work off the main actor (the python/script shell-outs).
    private func runOffMain(_ work: @escaping () -> Void) {
        Task.detached { work() }
    }

    private static func alertOnMain(_ message: String, _ info: String) {
        DispatchQueue.main.async {
            NSApp.activate(ignoringOtherApps: true)
            let alert = NSAlert()
            alert.messageText = message
            alert.informativeText = info
            alert.alertStyle = .warning
            alert.runModal()
        }
    }

    /// "Can't open this reference" variant — its hint depends on whether a Jira
    /// base is configured for the selected workspace.
    @MainActor
    fileprivate static func alertOnMain(_ message: String, refType: String, store: WorkspaceStore) {
        let info: String
        if refType == "jira" {
            info = "Set a Jira base URL for this workspace (Workspace → … → Set Jira base URL…) to open Jira keys."
        } else {
            info = "This reference (\(refType)) isn’t a link the app can open."
        }
        alertOnMain(message, info)
    }

    private func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    // MARK: - Menu item builders

    private func header(_ menu: NSMenu, _ title: String, symbol: String) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        let icon = NSImage(systemSymbolName: symbol, accessibilityDescription: title)
        icon?.isTemplate = true
        item.image = icon
        menu.addItem(item)
    }

    private func sectionHeader(_ menu: NSMenu, _ title: String) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(string: title.uppercased(), attributes: [
            .font: NSFont.systemFont(ofSize: 10, weight: .semibold),
            .foregroundColor: NSColor.secondaryLabelColor,
        ])
        item.isEnabled = false
        menu.addItem(item)
    }

    private func disabledRow(_ menu: NSMenu, _ text: String) {
        let item = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(string: text, attributes: [
            .font: NSFont.menuFont(ofSize: NSFont.smallSystemFontSize),
            .foregroundColor: NSColor.tertiaryLabelColor,
        ])
        item.isEnabled = false
        menu.addItem(item)
    }

    @discardableResult
    private func addItem(_ menu: NSMenu, _ title: String, _ action: Selector,
                         symbol: String? = nil, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        if let symbol { item.image = templateSymbol(symbol) }
        menu.addItem(item)
        return item
    }

    /// Task title: id + title in the full label color, the workdir appended dim as
    /// metadata, and — for a task whose worker is actually running — a trailing
    /// green "LIVE" badge. The badge is about *liveness* (a process is up), not
    /// status, so it carries information the In progress band doesn't.
    private func taskTitle(_ task: PWCTask, live: Bool) -> NSAttributedString {
        let font = NSFont.menuFont(ofSize: 0)
        let s = NSMutableAttributedString()
        s.append(NSAttributedString(string: task.id, attributes: [
            .font: font, .foregroundColor: NSColor.labelColor,
        ]))
        let titleText = task.title.count > 48 ? String(task.title.prefix(47)) + "…" : task.title
        s.append(NSAttributedString(string: "  \(titleText)", attributes: [
            .font: font, .foregroundColor: NSColor.labelColor,
        ]))
        if let dir = task.workdir, !dir.isEmpty {
            s.append(NSAttributedString(string: "   \(dir)", attributes: [
                .font: NSFont.monospacedSystemFont(ofSize: font.pointSize - 2, weight: .regular),
                .foregroundColor: NSColor.secondaryLabelColor,
            ]))
        }
        if live {
            let badge = NSTextAttachment()
            badge.image = Self.liveBadgeImage
            // Vertically center the badge image against the menu font.
            let h = Self.liveBadgeImage.size.height
            badge.bounds = NSRect(x: 0, y: (font.capHeight - h) / 2,
                                  width: Self.liveBadgeImage.size.width, height: h)
            s.append(NSAttributedString(string: "  "))
            s.append(NSAttributedString(attachment: badge))
        }
        return s
    }

    /// A small green rounded "LIVE" pill, rendered once and reused. Drawn at 2×
    /// into a retina-correct image so the corners stay crisp in the menu.
    static let liveBadgeImage: NSImage = {
        let text = "LIVE"
        let font = NSFont.systemFont(ofSize: 9, weight: .bold)
        let textSize = (text as NSString).size(withAttributes: [.font: font])
        let padX: CGFloat = 5, padY: CGFloat = 2
        let size = NSSize(width: ceil(textSize.width) + padX * 2,
                          height: ceil(textSize.height) + padY * 2)
        let image = NSImage(size: size)
        image.lockFocus()
        let rect = NSRect(origin: .zero, size: size)
        let radius = size.height / 2
        NSColor.systemGreen.setFill()
        NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
        (text as NSString).draw(
            at: NSPoint(x: padX, y: padY),
            withAttributes: [.font: font, .foregroundColor: NSColor.white])
        image.unlockFocus()
        image.isTemplate = false   // keep its green; not a template glyph
        return image
    }()

    private func templateSymbol(_ name: String) -> NSImage? {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: name)
        image?.isTemplate = true
        return image
    }

    /// A small filled dot in the given color (status / liveness cue).
    private func dot(_ color: NSColor) -> NSImage {
        let size = NSSize(width: 9, height: 9)
        let image = NSImage(size: size)
        image.lockFocus()
        color.setFill()
        NSBezierPath(ovalIn: NSRect(origin: .zero, size: size)).fill()
        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    /// Priority shown as a colored dot at the row edge: p1 red, p2 orange, p3
    /// yellow, lower grey — a cue, not a number to decode.
    private func priorityDot(_ priority: Int) -> NSImage {
        let color: NSColor
        switch priority {
        case ...1: color = .systemRed
        case 2: color = .systemOrange
        case 3: color = .systemYellow
        default: color = .tertiaryLabelColor
        }
        return dot(color)
    }

    // Exposed to TaskSubmenuController for building briefing rows.
    func makeSectionHeader(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(string: title.uppercased(), attributes: [
            .font: NSFont.systemFont(ofSize: 10, weight: .semibold),
            .foregroundColor: NSColor.secondaryLabelColor,
        ])
        item.isEnabled = false
        return item
    }

    /// Emphasis for a non-interactive informational row.
    enum RowEmphasis {
        case primary    // readable content (status line) — full label color
        case secondary  // briefing content (events) — secondary, still readable
        case hint       // genuinely incidental (empty/loading states) — dim
    }

    func makeDisabledRow(_ text: String, emphasis: RowEmphasis = .hint) -> NSMenuItem {
        let item = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        let color: NSColor
        let size: CGFloat
        switch emphasis {
        case .primary:   color = .labelColor;          size = 0
        case .secondary: color = .secondaryLabelColor; size = 0
        case .hint:      color = .tertiaryLabelColor;  size = NSFont.smallSystemFontSize
        }
        item.attributedTitle = NSAttributedString(string: text, attributes: [
            .font: NSFont.menuFont(ofSize: size),
            .foregroundColor: color,
        ])
        item.isEnabled = false
        return item
    }

    func makeTemplateSymbol(_ name: String) -> NSImage? { templateSymbol(name) }
}

/// Carries a (task id, value) pair on a menu item's representedObject for the
/// priority/status write actions.
final class TaskValue: NSObject {
    let task: String
    let value: String
    init(task: String, value: String) { self.task = task; self.value = value }
}

/// Owns one task row's submenu: builds the briefing (Latest events + References,
/// lazily fetched from `taskdb.py detail`) followed by the deterministic actions.
/// Lives as long as its submenu (held via objc associated object), so reopening a
/// task reuses the cached detail instead of re-shelling out.
@MainActor
final class TaskSubmenuController: NSObject, NSMenuDelegate {
    private let task: PWCTask
    private weak var parent: MenuBarController?
    private var detail: TaskDetail?
    private var fetching = false

    private let dateParser: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime]; return f
    }()

    init(task: PWCTask, parent: MenuBarController) {
        self.task = task
        self.parent = parent
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        // Kick off a one-time detail fetch; when it lands, rebuild in place.
        if detail == nil && !fetching {
            fetching = true
            parent?.loadDetail(task: task.id) { [weak self, weak menu] d in
                guard let self else { return }
                self.fetching = false
                self.detail = d
                if let menu, menu.numberOfItems > 0 { self.rebuild(menu) }
            }
        }
        rebuild(menu)
    }

    private func rebuild(_ menu: NSMenu) {
        guard let parent else { return }
        menu.removeAllItems()

        // Focus worker tab FIRST for a live task — the most common reason to open a
        // running worker's menu, always at the top in the same spot.
        if let focus = parent.makeFocusItem(for: task) {
            menu.addItem(focus)
            menu.addItem(.separator())
        }

        // Full title (untruncated).
        let title = NSMenuItem(title: task.title, action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        // Status line — readable (primary), it's the at-a-glance state.
        menu.addItem(parent.makeDisabledRow(
            "\(task.status) · p\(task.priority)\(task.workdir.map { " · \($0)" } ?? "")",
            emphasis: .primary))
        menu.addItem(.separator())

        // LATEST — the 3 most recent events (timeline is oldest-first; reverse).
        menu.addItem(parent.makeSectionHeader("Latest"))
        if let events = detail?.events, !events.isEmpty {
            for event in events.suffix(3).reversed() {
                let when = relativeAge(event.at)
                let text = event.detail ?? event.kind
                let row = parent.makeDisabledRow("· \(text)\(when.map { "   (\($0))" } ?? "")",
                                                 emphasis: .secondary)
                row.toolTip = "\(event.source) · \(event.kind) · \(event.at)"
                menu.addItem(row)
            }
        } else {
            menu.addItem(parent.makeDisabledRow(fetching ? "Loading…" : "No events"))
        }
        menu.addItem(.separator())

        // REFERENCES — clickable; opens the PR/Slack/url, or a built Jira link.
        if let refs = detail?.refs, !refs.isEmpty {
            menu.addItem(parent.makeSectionHeader("References"))
            for ref in refs {
                let label = ref.label ?? ref.value
                let row = NSMenuItem(title: label, action: #selector(openRef(_:)), keyEquivalent: "")
                row.target = self
                row.representedObject = ref
                row.image = parent.makeTemplateSymbol(symbol(for: ref.refType))
                row.toolTip = ref.isURL ? ref.value : "\(ref.refType): \(ref.value)"
                menu.addItem(row)
            }
            menu.addItem(.separator())
        }

        // The deterministic actions (Start/Focus/Priority/Status/Archive).
        parent.appendTaskActions(to: menu, task: task)
    }

    @objc private func openRef(_ sender: NSMenuItem) {
        guard let ref = sender.representedObject as? TaskRef else { return }
        parent?.openRef(ref)
    }

    private func symbol(for refType: String) -> String {
        switch refType {
        case "jira": return "ticket"
        case "slack": return "bubble.left.and.bubble.right"
        case "url", "github": return "arrow.up.right.square"
        default: return "link"
        }
    }

    /// Compact "2h"/"3d" age of an ISO8601 timestamp, or nil if unparseable.
    private func relativeAge(_ iso: String) -> String? {
        guard let date = dateParser.date(from: iso) else { return nil }
        let secs = Int(Date().timeIntervalSince(date))
        if secs < 60 { return "\(secs)s ago" }
        if secs < 3600 { return "\(secs / 60)m ago" }
        if secs < 86400 { return "\(secs / 3600)h ago" }
        return "\(secs / 86400)d ago"
    }
}
