import Foundation

/// Running the `rada` command, which is the only way this window knows anything.
///
/// There is no daemon to talk to and no socket: rada is a JSON file under ~/.rada and a
/// lock, and the rules for reading it are in Python. So the window shells out, the same
/// way a person would, and every action it takes is a command that could have been typed
/// instead. That also means the window cannot invent a state the command line disagrees
/// with, and that anything it does can be undone from a terminal.
enum Cli {
    static let pathKey = "radaPath"

    static var path: String {
        get { UserDefaults.standard.string(forKey: pathKey) ?? discovered }
        set {
            UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespaces),
                                      forKey: pathKey)
        }
    }

    static var isUsable: Bool { FileManager.default.isExecutableFile(atPath: path) }

    /// Where rada is, on a Mac where nobody has said.
    ///
    /// PATH is not enough. An application started from the Dock inherits a PATH with
    /// neither ~/.local/bin nor /opt/homebrew/bin in it, so the window found nothing
    /// while the same command worked in every terminal on the machine.
    static let discovered: String = {
        if let given = ProcessInfo.processInfo.environment["RADA_BIN"], !given.isEmpty {
            return given
        }
        var candidates: [String] = []
        if let raw = ProcessInfo.processInfo.environment["PATH"] {
            candidates += raw.split(separator: ":").map { "\($0)/rada" }
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        candidates += ["\(home)/.local/bin/rada", "/opt/homebrew/bin/rada",
                       "/usr/local/bin/rada", "/usr/bin/rada"]
        // A clone that was never installed: the app sits in macapp/ inside the repo.
        candidates.append(Bundle.main.bundleURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("bin/rada").path)
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
            ?? "\(home)/.local/bin/rada"
    }()

    struct Output {
        let status: Int32
        let out: Data
        let err: String
    }

    /// Run rada once and wait for it. Never throws: a window that raises because a
    /// command line is missing is a window that cannot tell you the command line is
    /// missing.
    static func run(_ arguments: [String]) async -> Output {
        let tool = path
        return await Task.detached(priority: .userInitiated) { () -> Output in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: tool)
            proc.arguments = arguments
            let out = Pipe(), err = Pipe()
            proc.standardOutput = out
            proc.standardError = err
            do {
                try proc.run()
            } catch {
                return Output(status: 127, out: Data(),
                              err: "Could not run \(tool): \(error.localizedDescription)")
            }
            // Read before waiting. A queue with twenty jobs in it fills the pipe, and a
            // process that waits first deadlocks against its own output.
            let data = out.fileHandleForReading.readDataToEndOfFile()
            let problem = err.fileHandleForReading.readDataToEndOfFile()
            proc.waitUntilExit()
            return Output(status: proc.terminationStatus, out: data,
                          err: String(data: problem, encoding: .utf8) ?? "")
        }.value
    }
}
