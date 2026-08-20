import Foundation

/// The queue as rada describes it, decoded from `rada status --json`.
///
/// Nothing in this file decides anything. Every sentence a person reads in the window
/// about why a job is waiting was written by rada/sched.py and arrives here as a string:
/// a second copy of the admission rule, in Swift, would be a second scheduler, and the
/// two would disagree on the day it mattered.
struct Harbour: Decodable, Equatable {
    let rada: String
    let now: Double
    let memory: Memory
    let sessions: String
    let running: [Running]
    let waiting: [Waiting]
    /// Jobs whose process is no longer there. They are not part of the queue any more;
    /// they are what is left of it, and a berth among them is still written down as
    /// spoken for until somebody lets go of it.
    let leftBehind: [LeftBehind]
    let judge: Judge
    let reservation: Reservation
    let learned: Int

    enum CodingKeys: String, CodingKey {
        case rada, now, memory, sessions, running, waiting, judge, reservation, learned
        case leftBehind = "left_behind"
    }

    /// The jobs that start as soon as they ask, in queue order.
    var starting: [Waiting] { waiting.filter { $0.willStart } }
    var blocked: [Waiting] { waiting.filter { !$0.willStart && !$0.held } }
    var held: [Waiting] { waiting.filter(\.held) }

    /// A lookup by ticket, for a window that holds an id across a redraw.
    func queued(_ id: String) -> Waiting? { waiting.first { $0.id == id } }
    func started(_ id: String) -> Running? { running.first { $0.id == id } }
    func abandoned(_ id: String) -> LeftBehind? { leftBehind.first { $0.id == id } }

    /// Memory written down as promised to jobs that are not there any more.
    var strandedBytes: Int { leftBehind.reduce(0) { $0 + $1.holding } }
}

struct Memory: Decodable, Equatable {
    let total: Int
    let used: Int
    /// What may still be handed out, before what is already promised is taken off it.
    let budget: Int
    let promised: Int
    let free: Int
    let pressure: Int
    let swapUsed: Int
    let swapTotal: Int
    /// The reasons rada forced the budget down, in its own words. Empty on a calm machine.
    let clamped: [String]
    let unknownPlatform: Bool

    enum CodingKeys: String, CodingKey {
        case total, used, budget, promised, free, pressure, clamped
        case swapUsed = "swap_used"
        case swapTotal = "swap_total"
        case unknownPlatform = "unknown_platform"
    }
}

struct Running: Decodable, Equatable, Identifiable {
    let id: String
    let project: String
    let command: String
    let need: Int
    /// The largest footprint measured so far, zero until the first sample lands.
    let peak: Int
    let startedAt: Double
    let seconds: Double
    let pid: Int?

    enum CodingKeys: String, CodingKey {
        case id, project, command, need, peak, seconds, pid
        case startedAt = "started_at"
    }
}

/// What the sweep would drop: a job whose process has gone.
struct LeftBehind: Decodable, Equatable, Identifiable {
    let id: String
    /// "lease" for a berth that was granted, "ticket" for one still waiting.
    let kind: String
    let project: String
    let command: String
    let need: Int
    let pid: Int?
    /// How long ago the process last said anything.
    let silentFor: Double
    /// Bytes still written down as promised to it. Zero for a ticket.
    let holding: Int

    var wasRunning: Bool { kind == "lease" }

    enum CodingKeys: String, CodingKey {
        case id, kind, project, command, need, pid, holding
        case silentFor = "silent_for"
    }
}

struct Waiting: Decodable, Equatable, Identifiable {
    let id: String
    let position: Int
    let project: String
    let cwd: String
    let command: String
    let note: String
    let need: Int
    /// True when a person typed the number rather than rada learning it.
    let declared: Bool
    let age: Double
    let queuedAt: Double
    let session: String
    let mandatory: Bool
    let judgeBonus: Double
    let hold: Hold?
    let force: Force?
    let willStart: Bool
    let why: String
    let held: Bool
    /// Nothing in the queue can free enough memory for this job: what is missing is
    /// held by programs rada does not manage.
    let impossibleForNow: Bool
    let blockers: [Blocker]

    enum CodingKeys: String, CodingKey {
        case id, position, project, cwd, command, note, need, declared, age, mandatory
        case hold, force, why, held, blockers, session
        case queuedAt = "queued_at"
        case judgeBonus = "judge_bonus"
        case willStart = "will_start"
        case impossibleForNow = "impossible_for_now"
    }

    enum State { case starting, forced, held, stuck, queued }

    var state: State {
        if held { return .held }
        if force != nil { return .forced }
        if willStart { return .starting }
        if impossibleForNow { return .stuck }
        return .queued
    }
}

struct Hold: Decodable, Equatable {
    let since: Double
    let note: String?
}

/// A force is either a moment or another job: `at` is when it may go, `after` is the
/// ticket it waits for. Exactly one of them is set.
struct Force: Decodable, Equatable {
    let at: Double?
    let after: String?
}

struct Blocker: Decodable, Equatable, Identifiable {
    let pid: Int
    let name: String
    let bytes: Int
    var id: Int { pid }
}

struct Judge: Decodable, Equatable {
    let at: Double
    let age: Double?
    let order: [String]
    let why: String

    var hasVerdict: Bool { !order.isEmpty }
}

struct Reservation: Decodable, Equatable {
    let id: String?
    let since: Double?
    let cooldownUntil: Double?
    let fails: Int

    enum CodingKeys: String, CodingKey {
        case id, since, fails
        case cooldownUntil = "cooldown_until"
    }
}

// MARK: - the same numbers the terminal prints

enum Format {
    /// rada's own sizes, powers of two, one decimal. A window that rounded differently
    /// from `rada status` would have somebody comparing two screens and finding two
    /// numbers for one thing.
    static func bytes(_ n: Int) -> String {
        var value = Double(n)
        for unit in ["B", "KB", "MB", "GB", "TB"] {
            if abs(value) < 1024 || unit == "TB" {
                return unit == "B" ? "\(Int(value))B"
                                   : String(format: "%.1f%@", value, unit)
            }
            value /= 1024
        }
        return String(format: "%.1fTB", value)
    }

    /// Seconds, in the largest unit that still says something useful.
    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        if s < 90 { return "\(s)s" }
        if s < 5400 { return "\(s / 60) min" }
        let hours = Double(s) / 3600
        return String(format: "%.1f h", hours)
    }

    /// The time of day something happened, which is what somebody comparing the window
    /// with their own terminal scrollback is actually looking for.
    static func time(_ epoch: Double) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: Date(timeIntervalSince1970: epoch))
    }
}
