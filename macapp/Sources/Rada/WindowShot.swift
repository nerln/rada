import AppKit
import SwiftUI

/// Saves a PNG of this window, for the README and the site.
///
/// Two things it is not. It is not `screencapture`, which needs the screen recording
/// permission, a large thing to hand out for the sake of a picture in a README; an
/// application asking for an image of its own window already owns those pixels. And it
/// is not a person clicking a menu: the pictures are made by a script that writes a demo
/// queue, opens this window against it and asks for a file, so regenerating them after a
/// change to the layout is one command rather than an afternoon.
///
///     RADA_HOME=/tmp/rada-demo ./Rada.app/Contents/MacOS/Rada \
///         --shot ../docs/img/01-queue.png --select 7dc146f1
///
/// With `--shot` the window takes its picture and quits. With SCRIBA-style RADA_SHOTS
/// pointing at a folder instead, the File menu grows an item and the shots are taken by
/// hand.
enum Shot {

    static var isEnabled: Bool { directory != nil }

    static var directory: URL? {
        guard let raw = ProcessInfo.processInfo.environment["RADA_SHOTS"], !raw.isEmpty
        else { return nil }
        return URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
    }

    // MARK: - the scripted form

    private static func argument(_ name: String) -> String? {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    /// Set the window up the way a picture wants it, photograph it, and quit.
    ///
    /// The wait before the shot is not superstition. The first read of the queue starts
    /// a Python interpreter, and a window photographed before it answers is a picture of
    /// an empty sidebar. It waits for the answer, then gives the layout one more beat.
    @MainActor
    static func arrange(store: QueueStore,
                        selection: Binding<ContentView.Selection?>,
                        forcing: Binding<Waiting?>,
                        holding: Binding<Waiting?>) {
        guard let target = argument("--shot") else { return }
        let wanted = argument("--select")
        let sheet = argument("--sheet")
        // Pinned, because it is not the machine's business what the documentation looks
        // like. Left to the system, four shots taken one minute apart came back as two
        // light and two dark.
        switch argument("--appearance") {
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        case "dark":  NSApp.appearance = NSAppearance(named: .darkAqua)
        default:      break
        }

        Task { @MainActor in
            // A window nobody brought forward is still drawn, but it is drawn behind
            // whatever asked for the picture, and the first shot came back with a
            // terminal in front of half of it.
            NSApp.activate(ignoringOtherApps: true)
            for _ in 0..<50 where store.harbour == nil {
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
            if let wanted, let harbour = store.harbour {
                // A ticket, a running job or something a vanished session left behind:
                // the picture asks for an id and the window has three lists to find it
                // in. Looking in only one of them produced a set of screenshots where
                // one of them silently showed no selection at all.
                if let job = harbour.queued(wanted) {
                    selection.wrappedValue = .waiting(job.id)
                    try? await Task.sleep(nanoseconds: 400_000_000)
                    switch sheet {
                    case "force": forcing.wrappedValue = job
                    case "hold":  holding.wrappedValue = job
                    default:      break
                    }
                } else if harbour.abandoned(wanted) != nil {
                    selection.wrappedValue = .abandoned(wanted)
                } else if harbour.started(wanted) != nil {
                    selection.wrappedValue = .running(wanted)
                }
            }
            try? await Task.sleep(nanoseconds: 900_000_000)
            let url = URL(fileURLWithPath: (target as NSString).expandingTildeInPath)
            let ok = write(to: url)
            print(ok ? "wrote \(url.path)" : "could not write \(url.path)")
            // exit rather than terminate. The ordinary shutdown asks each window whether
            // it may close, and a window with a sheet attached never answers: the shot of
            // the force panel came out fine and the process then sat there until it was
            // killed.
            exit(ok ? 0 : 1)
        }
    }

    // MARK: - the menu form

    @discardableResult
    static func save(named name: String? = nil) -> URL? {
        guard let dir = directory else { return nil }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent((name ?? nextName()) + ".png")
        guard write(to: url) else { NSSound.beep(); return nil }
        NSSound(named: "Grab")?.play()
        return url
    }

    /// PNG of the window this process owns.
    ///
    /// Asking the window server for the window's own image is the only version of this
    /// that comes back whole. Drawing the view hierarchy by hand skips whatever the
    /// compositor is responsible for, and on a NavigationSplitView that is the entire
    /// sidebar.
    @discardableResult
    private static func write(to url: URL) -> Bool {
        // A sheet is a window of its own. Photographing the parent alone gives a
        // picture of the window with the sheet missing, which is a picture of a
        // different program: the panel is the whole point of that shot. So the shot is
        // taken from a list, sheets first, since the array is in front-to-back order.
        guard let parent = NSApp.windows.first(where: {
            $0.isVisible && $0.sheetParent == nil && $0.className != "NSStatusBarWindow"
        }) ?? NSApp.keyWindow else { return false }
        let sheets = NSApp.windows.filter { $0.isVisible && $0.sheetParent === parent }
        var ids = (sheets + [parent]).map { UnsafeRawPointer(bitPattern: UInt($0.windowNumber)) }
        guard let list = CFArrayCreate(nil, &ids, ids.count, nil),
              let image = CGImage(windowListFromArrayScreenBounds: .null,
                                  windowArray: list,
                                  imageOption: [.boundsIgnoreFraming, .bestResolution])
        else { return false }
        let rep = NSBitmapImageRep(cgImage: image)
        guard let data = rep.representation(using: .png, properties: [:]) else { return false }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        return (try? data.write(to: url)) != nil
    }

    /// shot-01, shot-02. Overwriting the last one silently is how a README quietly
    /// loses a picture.
    private static func nextName() -> String {
        guard let dir = directory else { return "shot" }
        let taken = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        for n in 1...99 {
            let candidate = String(format: "shot-%02d", n)
            if !taken.contains(candidate + ".png") { return candidate }
        }
        return "shot"
    }
}
