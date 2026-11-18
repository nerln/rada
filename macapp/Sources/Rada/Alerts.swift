import AppKit
import OSLog
import SwiftUI
import UserNotifications

/// The notification rada sends when a job is stuck, and the two buttons on it.
///
/// It used to be `osascript display notification`, which has no identity: the banner was
/// headed "Script Editor", clicking it opened Script Editor, and the only thing it could
/// say was a sentence ending in "run: rada force 8f3a". A person reading it on a phone
/// screen's worth of banner had to go and find a terminal.
///
/// Now the banner belongs to Rada, and its buttons do the two things worth doing: start
/// the job past the budget, or open the queue and look. The force is the same command the
/// window's own button runs.
///
/// The app is launched for this with `open -g`, so it does not come forward and does not
/// take the keyboard. It puts its window away and drops to accessory, so no Dock icon
/// appears either: nothing on screen moves except the banner.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {

    /// Written to the system log rather than to standard output, because the app is
    /// started by LaunchServices and nobody is holding its output. `log show --predicate
    /// 'subsystem == "dev.nerelli.rada"'` reads it, and that is how the banner was
    /// tested on a machine where the screen could not be looked at.
    static let log = Logger(subsystem: "dev.nerelli.rada", category: "notice")

    /// Where to write the outcome, when somebody is testing. `RADA_NOTICE_OUT` names a
    /// file and the one line goes there as well as to the log above. The log turned out
    /// not to survive an immediate exit, and a banner cannot be looked at by a program.
    static var outPath: String? {
        let raw = ProcessInfo.processInfo.environment["RADA_NOTICE_OUT"]
        return (raw?.isEmpty == false) ? raw : nil
    }

    static func report(_ line: String) {
        log.notice("\(line, privacy: .public)")
        guard let path = outPath else { return }
        let text = line + "\n"
        if let handle = FileHandle(forWritingAtPath: path) {
            handle.seekToEndOfFile()
            handle.write(Data(text.utf8))
            try? handle.close()
        } else {
            try? text.write(toFile: path, atomically: true, encoding: .utf8)
        }
    }

    static let category = "dev.nerelli.rada.stuck"
    static let forceAction = "dev.nerelli.rada.force"
    static let openAction = "dev.nerelli.rada.open"

    /// Whether a person has had this app in front of them since it started.
    ///
    /// The whole question of whether to stay or go turns on this, and it used to turn on
    /// a clock: a url arriving in the first five seconds meant "launched for a notice".
    /// Both ways of being wrong were real. A launch slowed past five seconds by a machine
    /// deep in swap, which is exactly when jobs get stuck, stayed open for good with a
    /// window and a Dock icon nobody asked for; and a person who had just double-clicked
    /// the icon could see their window vanish because a notice arrived in the same
    /// seconds. What actually separates the two is that a person's launch makes the app
    /// active and `open -g` never does.
    private var personSeen = false
    private var launchedForNotice = false
    private var askedPerson = false
    private var lastTicket: (id: String, at: Date)?

    func applicationWillFinishLaunching(_ notification: Notification) {
        // Set before any window exists, because a notification whose button is pressed
        // tomorrow relaunches this app to deliver the answer, and the delegate has to be
        // in place by then or the press is dropped.
        let centre = UNUserNotificationCenter.current()
        centre.delegate = self
        centre.setNotificationCategories([
            UNNotificationCategory(
                identifier: Self.category,
                actions: [
                    UNNotificationAction(identifier: Self.forceAction,
                                         title: "Start it anyway",
                                         options: []),
                    UNNotificationAction(identifier: Self.openAction,
                                         title: "Open the queue",
                                         options: [.foreground]),
                ],
                intentIdentifiers: [],
                options: [])
        ])
        // The url is taken here, before SwiftUI sees it. Left to SwiftUI, a url sent to a
        // running app can be answered with a new window, which is the one thing a notice
        // must never do. If SwiftUI installs its own handler after this one, `onOpenURL`
        // on the window still reaches `handle(url:)`, and the repeat is dropped there.
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(gotURL(_:withReply:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL))
    }

    @objc private func gotURL(_ event: NSAppleEventDescriptor,
                              withReply reply: NSAppleEventDescriptor) {
        guard let text = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
              let url = URL(string: text) else { return }
        handle(url: url)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // A process nobody ever looked at has no reason to exist after a few seconds.
        // Whatever started it in the background (a notice, a button pressed on an old
        // banner, a url SwiftUI did not pass on) has had time to do its work by then.
        // This is the backstop for every road that forgets to quit, which is the bug
        // that made the app look as if it would not open.
        let scripted = Shot.isEnabled || CommandLine.arguments.contains("--shot")
            || CommandLine.arguments.contains("--notice")
        guard !scripted else { return }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 12_000_000_000)
            if !personSeen && !NSApp.isActive {
                Self.report("nobody opened this window; leaving")
                NSApp.terminate(nil)
            }
        }
    }

    /// A person is looking. From here on this is their window, nothing quits it but
    /// them, and it is the right moment to ask for notifications.
    ///
    /// Asking is two different acts, and doing only one of them left the first stuck job
    /// silent. Launched for a banner, the app asks provisionally, which macOS grants
    /// without a dialog and delivers quietly to the notification centre. With a person in
    /// front of it, it asks for the ordinary kind, which is when a dialog is a reasonable
    /// thing to see, and which is what turns later notices into banners.
    func applicationDidBecomeActive(_ notification: Notification) {
        personSeen = true
        launchedForNotice = false
        guard !askedPerson else { return }
        askedPerson = true
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            let centre = UNUserNotificationCenter.current()
            _ = try? await centre.requestAuthorization(options: [.alert, .sound])
            Self.recordNotices(await centre.notificationSettings().authorizationStatus)
        }
    }

    /// Nobody is looking at this process: it was started or reached in the background.
    private var unseen: Bool { !personSeen && !NSApp.isActive }

    /// A job is stuck, and the id of the ticket is all this carries.
    ///
    /// Deliberately all it carries. A URL scheme is openable by any page in any browser,
    /// so nothing in the URL is believed: the id must look like an id, and the queue is
    /// then read to see whether that job is really there and really waiting. The worst a
    /// hostile page can achieve is a banner about a job that is genuinely stuck.
    /// Reached from `onOpenURL` on the window, not from this object's own
    /// `application(_:open:)`. With the SwiftUI lifecycle that delegate method is never
    /// called: the url was sent, the app launched, and nothing at all happened, which
    /// took a round of the system log to notice.
    func handle(url: URL) {
        guard url.scheme == "rada", let ticket = Self.ticket(in: url) else {
            Self.report("ignoring \(url.absoluteString)")
            return
        }
        // Reached twice when both the event handler and `onOpenURL` get it.
        if let last = lastTicket, last.id == ticket, Date().timeIntervalSince(last.at) < 3 {
            return
        }
        lastTicket = (ticket, Date())
        Self.report("url for \(ticket)")
        if unseen { stepBack() }
        Task { await announce(ticket: ticket) }
    }

    /// `rada://stuck/8f3a`, and nothing else. Short lower-case hex, which is what
    /// `cli.new_id` makes.
    static func ticket(in url: URL) -> String? {
        guard url.host == "stuck" else { return nil }
        let candidate = url.lastPathComponent
        guard candidate.count >= 4, candidate.count <= 16,
              candidate.allSatisfy({ $0.isHexDigit && !$0.isUppercase })
        else { return nil }
        return candidate
    }

    /// Out of the way: window hidden rather than closed, so pressing "Open the queue"
    /// has something to bring back, and no Dock icon for a banner nobody asked to see.
    private func stepBack() {
        launchedForNotice = true
        NSApp.setActivationPolicy(.accessory)
        for window in NSApp.windows { window.orderOut(nil) }
        // However the work in between ends, a process in this state does not outlive it
        // by long. `announce` quits after its banner; this is for anything that hangs.
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 20_000_000_000)
            if launchedForNotice && !personSeen { NSApp.terminate(nil) }
        }
    }

    /// A click on the Dock icon, in Finder or from Spotlight, while the process is
    /// already alive. Whatever state it was left in, the answer is the window: this is
    /// the person asking to see the queue, and a process that keeps its window to itself
    /// is indistinguishable, from their side, from an app that will not open.
    ///
    /// Returning false when a hidden window was brought back, and true only when there
    /// was none, so AppKit makes a new one then and never a second one beside it.
    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows flag: Bool) -> Bool {
        !comeForward()
    }

    /// Back in front, as an ordinary app. True when there was a window to show.
    @discardableResult
    private func comeForward() -> Bool {
        personSeen = true
        launchedForNotice = false
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        let windows = NSApp.windows.filter { $0.canBecomeMain && $0.styleMask.contains(.titled) }
        guard let window = windows.first(where: { $0.title == "rada" }) ?? windows.first
        else { return false }
        window.makeKeyAndOrderFront(nil)
        return true
    }

    /// Launched for a notice and nothing else, the process says its piece and goes,
    /// whatever the piece turned out to be.
    ///
    /// It used to leave from the middle. When notifications were refused, or the job was
    /// no longer waiting, the function returned before it reached the line that quits,
    /// and the process stayed: no window, no Dock icon, its window ordered out. Every
    /// later double click went to that process, which had nothing to show, so the app
    /// looked as if it would not start at all, and every later stuck job reached the
    /// same invisible copy. Delivering and leaving are now two functions, so no answer
    /// from the first can skip the second.
    @discardableResult
    private func announce(ticket: String) async -> String {
        let outcome = await deliver(ticket: ticket)
        Self.report("notice for \(ticket): \(outcome)")
        await leaveIfUnseen()
        return outcome
    }

    /// Read the queue before saying anything, so the banner cannot describe a job that
    /// is not waiting. Says nothing at all when the ticket is gone, which is the honest
    /// answer to a stale or invented URL.
    private func deliver(ticket: String) async -> String {
        let centre = UNUserNotificationCenter.current()
        // Provisional costs nothing and asks nobody. Without it the first notice of all
        // is the one that goes missing, which is the one that matters.
        do {
            let granted = try await centre.requestAuthorization(
                options: [.alert, .sound, .provisional])
            Self.report("asked: granted=\(granted)")
        } catch {
            Self.report("asked: refused with \(error)")
        }
        let settings = await centre.notificationSettings()
        Self.recordNotices(settings.authorizationStatus)
        if settings.authorizationStatus == .denied {
            return "refused: notifications are turned off for Rada"
        }

        let result = await Cli.run(["status", "--json"])
        guard result.status == 0,
              let harbour = try? JSONDecoder().decode(Harbour.self, from: result.out),
              let job = harbour.queued(ticket)
        else { return "nothing waiting under that ticket" }

        let content = UNMutableNotificationContent()
        content.title = "A job cannot fit"
        content.subtitle = job.project.isEmpty ? job.command : job.project
        // One banner speaks for all of them. The command line sends at most one notice
        // every ten minutes for the whole machine, because six sessions with six stuck
        // jobs used to mean six banners and six launches of this app in the same minute.
        let others = harbour.waiting.filter { $0.impossibleForNow && $0.id != ticket }.count
        content.body = [
            job.command,
            "waited \(Format.duration(job.age)), wants \(Format.bytes(job.need))",
            job.blockers.isEmpty ? "" :
                "held by " + job.blockers.prefix(2).map(\.name).joined(separator: ", "),
            others == 0 ? "" : (others == 1 ? "1 more job cannot fit either"
                                            : "\(others) more jobs cannot fit either"),
        ].filter { !$0.isEmpty }.joined(separator: "\n")
        content.categoryIdentifier = Self.category
        content.userInfo = ["ticket": ticket]
        content.sound = .default

        // The id is the ticket, so a job that gets stuck twice replaces its own banner
        // instead of stacking a second one on top.
        let request = UNNotificationRequest(identifier: "stuck-\(ticket)",
                                            content: content, trigger: nil)
        do {
            try await centre.add(request)
        } catch {
            return "could not be delivered: \(error.localizedDescription)"
        }
        let delivered = await centre.deliveredNotifications()
            .contains { $0.request.identifier == "stuck-\(ticket)" }
        return (delivered ? "delivered" : "accepted but not in the centre yet")
            + ", authorization \(Self.describe(settings.authorizationStatus))"
    }

    /// Where rada keeps its state, which is where the command line will look.
    static var radaHome: String {
        let raw = ProcessInfo.processInfo.environment["RADA_HOME"] ?? "~/.rada"
        return (raw as NSString).expandingTildeInPath
    }

    /// Tells the command line whether handing this app a notice can produce a banner.
    ///
    /// Without it the command line launched the app for every stuck job on a machine
    /// where notifications had been refused, and each launch could do nothing but read
    /// the refusal and leave. `~/.rada/app.json` says what the app last learned, and
    /// `rada/cli.py` only hands a notice over when it says granted or provisional.
    static func recordNotices(_ status: UNAuthorizationStatus) {
        let body: [String: Any] = ["notices": describe(status),
                                   "at": Date().timeIntervalSince1970]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return }
        try? FileManager.default.createDirectory(atPath: radaHome,
                                                 withIntermediateDirectories: true)
        let path = (radaHome as NSString).appendingPathComponent("app.json")
        try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
    }

    static func describe(_ status: UNAuthorizationStatus) -> String {
        switch status {
        case .authorized: return "granted"
        case .provisional: return "provisional"
        case .denied: return "denied"
        case .notDetermined: return "not asked"
        default: return "unknown"
        }
    }

    /// The same banner, asked for from a terminal, so that it can be tested by somebody
    /// who cannot see the screen. `Rada.app/Contents/MacOS/Rada --notice <ticket>` says
    /// on standard output what happened and exits. The url above is the road production
    /// uses; this is the same function reached another way.
    private var noticed = false

    func noticeFromCommandLine(ticket: String) {
        // The window group hands this to every window it makes, and it makes two, so
        // without the guard the same banner was posted twice and the log said everything
        // twice as well.
        guard !noticed else { return }
        noticed = true
        launchedForNotice = true
        NSApp.setActivationPolicy(.accessory)
        Task { @MainActor in
            let outcome = await announce(ticket: ticket)
            print(outcome)
            // A moment for the write above to land before the process goes.
            try? await Task.sleep(nanoseconds: 300_000_000)
            exit(outcome.hasPrefix("delivered") || outcome.hasPrefix("accepted") ? 0 : 1)
        }
    }

    // MARK: - the buttons

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        let ticket = response.notification.request.content.userInfo["ticket"] as? String
        // A button pressed on a banner whose app has long quit relaunches the app in the
        // background to hear it. "Start it anyway" carries no request to come forward,
        // so that launch steps back and leaves like a notice launch does, instead of
        // opening a window and a Dock icon behind the person's back.
        switch response.actionIdentifier {
        case Self.forceAction:
            if unseen { stepBack() }
            guard let ticket else { await leaveIfUnseen(); return }
            let done = await Cli.run(["force", ticket])
            // Tell them it happened, or that it did not. A button that answers with
            // nothing is a button you press twice.
            let said = String(data: done.out, encoding: .utf8) ?? ""
            await note(done.status == 0
                       ? (said.split(separator: "\n").first.map(String.init)
                          ?? "\(ticket) will start now")
                       : "That job is no longer waiting.")
            await leaveIfUnseen()
        case Self.openAction, UNNotificationDefaultActionIdentifier:
            comeForward()
            NotificationCenter.default.post(name: .radaRefresh, object: nil)
        default:
            if unseen { stepBack() }
            await leaveIfUnseen()
        }
    }

    /// The one way a background launch ends: a moment for the last banner to be handed
    /// over, then quit, unless a person brought the window forward meanwhile.
    private func leaveIfUnseen() async {
        // `--notice` from a terminal prints its outcome and exits with a status of its
        // own, so it is left to do that.
        guard launchedForNotice, !noticed else { return }
        try? await Task.sleep(nanoseconds: 1_500_000_000)
        if launchedForNotice && !personSeen { NSApp.terminate(nil) }
    }

    /// Banners while the app is in front are suppressed by macOS unless this says
    /// otherwise, and the one case that matters is the window being open when a job in
    /// it gets stuck.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    private func note(_ text: String) async {
        let content = UNMutableNotificationContent()
        content.title = "rada"
        content.body = text
        try? await UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: content,
                                  trigger: nil))
    }
}
