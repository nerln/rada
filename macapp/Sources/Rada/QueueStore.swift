import Foundation
import SwiftUI

/// Reads the queue, and carries out the two things a person can decide about it.
///
/// Polling rather than watching the file: rada's state changes when somebody else's
/// process takes the lock, and the interesting quantities, free memory and how long a
/// job has waited, move on their own with no write at all. Two seconds is slower than
/// anybody can act and cheap enough that the reading never shows up in the queue it is
/// reporting on.
@MainActor
final class QueueStore: ObservableObject {
    @Published private(set) var harbour: Harbour?
    /// Set when rada could not be asked. Kept apart from an empty queue on purpose: the
    /// two look identical in a list and mean opposite things.
    @Published private(set) var problem: String?
    @Published private(set) var lastRead: Date?
    /// What the last force or hold printed, shown under the queue for a few seconds.
    @Published var receipt: String?

    private var reading = false
    private var busy = 0

    var isWorking: Bool { busy > 0 }

    func reload() {
        guard !reading else { return }
        reading = true
        Task {
            let result = await Cli.run(["status", "--json"])
            reading = false
            guard result.status == 0 else {
                problem = failure(result)
                return
            }
            do {
                let fresh = try JSONDecoder().decode(Harbour.self, from: result.out)
                problem = nil
                lastRead = Date()
                if fresh != harbour { harbour = fresh }
            } catch {
                // Do not blank the window over one unreadable answer. The queue on
                // screen is a second old and still true; a sidebar that empties itself
                // reads as "everything finished", which is the opposite of what happened.
                problem = "rada answered with something this window could not read."
            }
        }
    }

    private func failure(_ result: Cli.Output) -> String {
        if !Cli.isUsable {
            return "No rada at \(Cli.path). Open Settings and point this at the command."
        }
        let said = result.err.trimmingCharacters(in: .whitespacesAndNewlines)
        return said.isEmpty ? "rada exited with code \(result.status)." : said
    }

    // MARK: - the two decisions

    /// Start a job the budget refuses, now or on a condition.
    func force(_ id: String, seconds: Double? = nil, after: String? = nil) async {
        var arguments = ["force", id]
        if let seconds, seconds > 0 { arguments += ["--in", String(Int(seconds))] }
        if let after { arguments += ["--after", after] }
        await act(arguments)
    }

    func cancelForce(_ id: String) async { await act(["force", id, "--cancel"]) }

    /// Keep a job from starting until somebody says otherwise.
    func hold(_ id: String, note: String) async {
        var arguments = ["hold", id]
        let trimmed = note.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty { arguments += ["--note", trimmed] }
        await act(arguments)
    }

    func release(_ id: String) async { await act(["hold", id, "--release"]) }

    /// Let go of every job whose process has gone. Nothing alive is touched, which is
    /// why this is one button and not one per row.
    func reap() async { await act(["reap"]) }

    /// One command, and then the queue is read again straight away.
    ///
    /// The reading is what updates the window. Nothing here edits the queue in place,
    /// so the window can never show a job as held because it was asked to be: it shows
    /// it as held once rada says so.
    private func act(_ arguments: [String]) async {
        busy += 1
        let result = await Cli.run(arguments)
        busy -= 1
        let said = (String(data: result.out, encoding: .utf8) ?? "")
            .split(separator: "\n").first.map(String.init) ?? ""
        if result.status != 0 {
            problem = result.err.trimmingCharacters(in: .whitespacesAndNewlines)
        } else {
            receipt = said
            problem = nil
        }
        reading = false
        reload()
    }
}
