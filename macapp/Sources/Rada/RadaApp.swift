import SwiftUI

@main
struct RadaApp: App {
    var body: some Scene {
        WindowGroup("rada") {
            ContentView()
        }
        .windowToolbarStyle(.unified)
        // Wide enough that a command line and its reason are both readable without
        // dragging the window on first launch.
        .defaultSize(width: 1120, height: 720)
        .commands {
            CommandGroup(replacing: .newItem) { }
            // The three things this window does, on keys that do not collide with the
            // ones a Mac application already owns. Command-F is Find everywhere else,
            // so forcing is not on it however much it looks like the obvious letter.
            CommandMenu("Queue") {
                Button("Refresh") {
                    NotificationCenter.default.post(name: .radaRefresh, object: nil)
                }
                .keyboardShortcut("r", modifiers: .command)
                Divider()
                Button("Start it anyway") {
                    NotificationCenter.default.post(name: .radaForce, object: nil)
                }
                .keyboardShortcut("s", modifiers: [.command, .shift])
                Button("Hold or release") {
                    NotificationCenter.default.post(name: .radaHold, object: nil)
                }
                .keyboardShortcut("h", modifiers: [.command, .shift])
            }
            if Shot.isEnabled {
                CommandGroup(after: .saveItem) {
                    Button("Save window as PNG") { Shot.save() }
                        .keyboardShortcut("p", modifiers: [.command, .option])
                }
            }
        }

        Settings {
            SettingsView()
        }
    }
}

extension Notification.Name {
    /// Posted by the menu. The window listens, and the menu items know nothing about
    /// the queue.
    static let radaRefresh = Notification.Name("dev.nerelli.rada.refresh")
    static let radaForce = Notification.Name("dev.nerelli.rada.force")
    static let radaHold = Notification.Name("dev.nerelli.rada.hold")
}

struct SettingsView: View {
    @State private var path = Cli.path

    var body: some View {
        Form {
            Section("The rada command") {
                TextField("Path", text: $path)
                    .onChange(of: path) { _, value in Cli.path = value }
                Text(state)
                    .font(.caption)
                    .foregroundStyle(FileManager.default.isExecutableFile(atPath: path)
                                     ? Color.secondary : Color.red)
                Text("This window runs that command and shows what it answers. Anything "
                     + "it does here can be done in a terminal, and anything done in a "
                     + "terminal shows up here within two seconds.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Where the queue lives") {
                Text(home)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                Text("One JSON file and a lock, shared by every session on this Mac. "
                     + "There is no daemon: closing this window leaves the queue exactly "
                     + "as it was.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520, height: 330)
    }

    private var state: String {
        FileManager.default.isExecutableFile(atPath: path)
            ? "Found."
            : "Nothing runnable there. rada installs to ~/.local/bin/rada with pip, and "
            + "a clone has it at bin/rada."
    }

    private var home: String {
        ProcessInfo.processInfo.environment["RADA_HOME"]
            ?? "\(FileManager.default.homeDirectoryForCurrentUser.path)/.rada"
    }
}
