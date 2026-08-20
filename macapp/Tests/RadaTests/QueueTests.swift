import XCTest
@testable import Rada

/// The window is a renderer for a document another program writes, so what is worth
/// testing is the reading of it. The fixture below is the output of
/// `rada status --json` against the demo queue in tools/schermate.py, trimmed of
/// nothing: when the shape changes, this fails before the window does.
final class QueueTests: XCTestCase {

    private let sample = """
    {"v":1,"rada":"0.1.0","now":1000.0,
     "memory":{"total":17179869184,"used":11166914969,"budget":3221225472,
               "promised":2147483648,"free":1073741824,"reserve":2576980377,
               "pressure":1,"jetsam":61,"swap_used":429496729,"swap_total":6442450944,
               "clamped":[],"unknown_platform":false},
     "sessions":"3 sessions open, so the queue is on",
     "running":[{"id":"a41f9c02","project":"aidirector","command":"ffmpeg -i a.mov out.mp4",
                 "need":2147483648,"peak":1932735283,"started_at":786.0,"seconds":214.0,
                 "pid":4242}],
     "left_behind":[{"id":"0c77b41e","kind":"lease","project":"kart-highlights",
                     "command":"ffmpeg -i race.mov cut.mp4","need":2147483648,"pid":99999,
                     "last_seen":-2120.0,"silent_for":3120.0,"holding":2147483648}],
     "waiting":[
       {"id":"7dc146f1","position":1,"project":"mechint","cwd":"/Users/x/mechint",
        "command":"python3 exp22.py","note":"the null control a paper is blocked on",
        "need":6442450944,"declared":false,"queued_at":-840.0,"age":1840.0,"session":"s1",
        "mandatory":true,"judge_bonus":3.0,"hold":null,"force":null,"will_start":false,
        "why":"first in the queue, waiting for 6.0GB; 1.0GB free so far","held":false,
        "impossible_for_now":false,"blockers":[]},
       {"id":"91c23663","position":2,"project":"molo","cwd":"/Users/x/molo",
        "command":"make check","note":"","need":536870912,"declared":false,
        "queued_at":959.0,"age":41.0,"session":"s2","mandatory":false,"judge_bonus":1.5,
        "hold":null,"force":{"at":990.0,"after":null},"will_start":true,
        "why":"forced by you","held":false,"impossible_for_now":false,"blockers":[]},
       {"id":"3b90ae55","position":3,"project":"OliveraXR3","cwd":"/Users/x/o",
        "command":"xcodebuild -scheme Olivera","note":"","need":4294967296,
        "declared":true,"queued_at":740.0,"age":260.0,"session":"s3","mandatory":false,
        "judge_bonus":0.0,"hold":null,"force":null,"will_start":false,
        "why":"needs 4.0GB and at most 1.0GB could be freed","held":false,
        "impossible_for_now":true,
        "blockers":[{"pid":901,"name":"Xcode","bytes":3221225472}]},
       {"id":"c5e0d418","position":4,"project":"vesuvius","cwd":"/Users/x/v",
        "command":"python3 reindex.py","note":"","need":3221225472,"declared":false,
        "queued_at":380.0,"age":620.0,"session":"s4",
        "mandatory":true,"judge_bonus":0.0,
        "hold":{"since":700.0,"note":"not while the disk is full"},"force":null,
        "will_start":false,
        "why":"held by you: it keeps its place in the queue and does not start, not while the disk is full",
        "held":true,"impossible_for_now":false,"blockers":[]}],
     "judge":{"at":978.0,"age":22.0,"order":["7dc146f1","91c23663","3b90ae55"],
              "why":"the experiment blocks a paper"},
     "reservation":{"id":"7dc146f1","since":880.0,"cooldown_until":null,"fails":0},
     "learned":3}
    """

    private func decoded() throws -> Harbour {
        try JSONDecoder().decode(Harbour.self, from: Data(sample.utf8))
    }

    func testTheQueueIsReadWhole() throws {
        let harbour = try decoded()
        XCTAssertEqual(harbour.running.count, 1)
        XCTAssertEqual(harbour.waiting.count, 4)
        XCTAssertEqual(harbour.memory.free, 1024 * 1024 * 1024)
        XCTAssertEqual(harbour.judge.order.count, 3)
        XCTAssertEqual(harbour.reservation.id, "7dc146f1")
    }

    /// The three groups the sidebar is built from. A job that is held must never appear
    /// among the ones about to start, whatever else is true of it.
    func testJobsAreGroupedByWhatHappensNext() throws {
        let harbour = try decoded()
        XCTAssertEqual(harbour.starting.map(\.id), ["91c23663"])
        XCTAssertEqual(harbour.blocked.map(\.id), ["7dc146f1", "3b90ae55"])
        XCTAssertEqual(harbour.held.map(\.id), ["c5e0d418"])
    }

    /// The report this section exists for: a queue full of jobs nobody recognised,
    /// which turned out to be berths belonging to sessions that had closed.
    func testWhatAVanishedSessionLeftIsKeptApart() throws {
        let harbour = try decoded()
        XCTAssertEqual(harbour.leftBehind.count, 1)
        XCTAssertEqual(harbour.leftBehind.first?.project, "kart-highlights")
        XCTAssertTrue(harbour.leftBehind.first?.wasRunning == true)
        XCTAssertEqual(harbour.strandedBytes, 2 * 1024 * 1024 * 1024)
        // and it is nowhere near the live queue
        XCTAssertNil(harbour.queued("0c77b41e"))
        XCTAssertNil(harbour.started("0c77b41e"))
    }

    func testEachJobKnowsWhichIconItGets() throws {
        let harbour = try decoded()
        XCTAssertEqual(harbour.queued("c5e0d418")?.state, .held)
        XCTAssertEqual(harbour.queued("91c23663")?.state, .forced)
        XCTAssertEqual(harbour.queued("3b90ae55")?.state, .stuck)
        XCTAssertEqual(harbour.queued("7dc146f1")?.state, .queued)
    }

    func testTheReasonIsRadasOwnSentence() throws {
        let harbour = try decoded()
        guard let held = harbour.queued("c5e0d418") else { return XCTFail("no such job") }
        XCTAssertTrue(held.why.hasPrefix("held by you"))
        XCTAssertEqual(held.hold?.note, "not while the disk is full")
        XCTAssertFalse(held.willStart)
    }

    func testWhoIsHoldingTheMemoryComesThrough() throws {
        let harbour = try decoded()
        guard let stuck = harbour.queued("3b90ae55") else { return XCTFail("no such job") }
        XCTAssertEqual(stuck.blockers.first?.name, "Xcode")
        XCTAssertEqual(stuck.blockers.first?.bytes, 3 * 1024 * 1024 * 1024)
        XCTAssertTrue(stuck.declared)
    }

    /// The window and the terminal have to agree on every number a person can compare.
    /// These are the strings rada/mem.py prints for the same values.
    func testSizesAreWrittenTheWayRadaWritesThem() {
        XCTAssertEqual(Format.bytes(6 * 1024 * 1024 * 1024), "6.0GB")
        XCTAssertEqual(Format.bytes(512 * 1024 * 1024), "512.0MB")
        XCTAssertEqual(Format.bytes(1932735283), "1.8GB")
        XCTAssertEqual(Format.bytes(0), "0B")
    }

    func testWaitsAreWrittenForSomebodyStandingThere() {
        XCTAssertEqual(Format.duration(41), "41s")
        XCTAssertEqual(Format.duration(620), "10 min")
        XCTAssertEqual(Format.duration(7200), "2.0 h")
    }

    /// An empty machine is the first thing anybody sees, and it must not be an error.
    func testNothingQueuedDecodes() throws {
        let empty = """
        {"v":1,"rada":"0.1.0","now":1.0,
         "memory":{"total":1,"used":0,"budget":1,"promised":0,"free":1,"reserve":0,
                   "pressure":1,"jetsam":90,"swap_used":0,"swap_total":0,"clamped":[],
                   "unknown_platform":false},
         "sessions":"one session on its own, so commands do not go through the queue",
         "running":[],"waiting":[],"left_behind":[],
         "judge":{"at":0,"age":null,"order":[],"why":""},
         "reservation":{"id":null,"since":null,"cooldown_until":null,"fails":0},
         "learned":0}
        """
        let harbour = try JSONDecoder().decode(Harbour.self, from: Data(empty.utf8))
        XCTAssertTrue(harbour.waiting.isEmpty)
        XCTAssertFalse(harbour.judge.hasVerdict)
        XCTAssertNil(harbour.reservation.id)
    }
}
