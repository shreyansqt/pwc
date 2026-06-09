import SwiftUI
import AppKit

@main
struct PWCBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        // Menu-bar-only: the entire UI is the NSStatusItem + NSMenu managed by
        // MenuBarController in the AppDelegate. We expose no SwiftUI scenes.
        Settings { EmptyView() }
    }
}

/// Owns the model + the native menu controller for the app's lifetime.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let store = WorkspaceStore()
    private lazy var board = BoardModel(store: store)
    private var menuController: MenuBarController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Log.info("PWCBar launched")
        menuController = MenuBarController(store: store, board: board)
    }
}
