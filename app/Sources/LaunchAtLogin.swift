import Foundation
import ServiceManagement

/// Thin wrapper over `SMAppService.mainApp` (macOS 13+) for the "Open at Login"
/// toggle. Registering adds PWCBar as a login item; the user can still flip it
/// off in System Settings → General → Login Items, so `isEnabled` reads the live
/// status rather than a stored preference.
enum LaunchAtLogin {
    /// Whether the app is currently registered to launch at login.
    static var isEnabled: Bool {
        SMAppService.mainApp.status == .enabled
    }

    /// Register or unregister the app as a login item. Throws if the system
    /// rejects the change (e.g. the user disabled it and it now requires approval
    /// in System Settings).
    static func setEnabled(_ enabled: Bool) throws {
        let service = SMAppService.mainApp
        if enabled {
            // register() is a no-op if already enabled; guard the requires-approval
            // case so a re-register doesn't throw.
            if service.status != .enabled { try service.register() }
        } else {
            if service.status == .enabled { try service.unregister() }
        }
    }
}
