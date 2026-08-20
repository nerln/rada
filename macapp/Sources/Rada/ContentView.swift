import SwiftUI

/// The window.
///
/// The queue is on the left, grouped by what is about to happen to each job rather than
/// by anything else: what is running, what starts as soon as it asks, what is waiting,
/// and what a person has held. The panel on the right is one job's reasons, in rada's
/// own words, with the two things a person can decide about it underneath.
///
/// Nothing in this window kills anything, and nothing here reorders the queue by hand.
/// The queue is rada's; a person can let a job through the budget, or keep it out.
struct ContentView: View {
    @StateObject private var store = QueueStore()
    @State private var selection: Selection?
    @State private var forcing: Waiting?
    @State private var holding: Waiting?

    /// The queue is read every two seconds. Ages and free memory move without anybody
    /// writing anything, so a window that only redrew on change would sit there saying
    /// a job had waited four minutes for as long as you looked at it.
    private let tick = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    enum Selection: Hashable {
        case running(String)
        case waiting(String)
        case abandoned(String)
    }

    var body: some View {
        // The bar sits above the split view rather than inside its safe area. As an
        // inset it laid over the top of both lists instead of moving them down, and the
        // first row of the queue was behind it.
        VStack(spacing: 0) {
            header
            NavigationSplitView {
                sidebar
                    .navigationSplitViewColumnWidth(min: 300, ideal: 360, max: 460)
            } detail: {
                detail
            }
        }
        .frame(minWidth: 980, minHeight: 620)
        .onAppear {
            store.reload()
            Shot.arrange(store: store, selection: $selection, forcing: $forcing,
                         holding: $holding)
        }
        .onReceive(tick) { _ in store.reload() }
        .onReceive(NotificationCenter.default.publisher(
            for: NSApplication.didBecomeActiveNotification)) { _ in store.reload() }
        .onReceive(NotificationCenter.default.publisher(for: .radaRefresh)) { _ in
            store.reload()
        }
        .onReceive(NotificationCenter.default.publisher(for: .radaForce)) { _ in
            if let job = selectedWaiting { forcing = job }
        }
        .onReceive(NotificationCenter.default.publisher(for: .radaHold)) { _ in
            guard let job = selectedWaiting else { return }
            if job.held {
                Task { await store.release(job.id) }
            } else {
                holding = job
            }
        }
        .sheet(item: $forcing) { job in
            ForceSheet(job: job, running: store.harbour?.running ?? []) { seconds, after in
                Task { await store.force(job.id, seconds: seconds, after: after) }
            }
        }
        .sheet(item: $holding) { job in
            HoldSheet(job: job) { note in
                Task { await store.hold(job.id, note: note) }
            }
        }
        .toolbar { toolbar }
    }

    // MARK: - header

    @ViewBuilder
    private var header: some View {
        VStack(spacing: 0) {
            if let harbour = store.harbour {
                HarbourBar(memory: harbour.memory, sessions: harbour.sessions,
                           reservation: harbour.reservation, now: harbour.now)
            }
            if let harbour = store.harbour, !harbour.leftBehind.isEmpty {
                HStack(spacing: 10) {
                    Image(systemName: "clock.badge.xmark")
                        .foregroundStyle(.secondary)
                    Text(strandedLine(harbour))
                        .font(.callout)
                    Spacer()
                    Button("Let go of them") { Task { await store.reap() } }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(.thinMaterial)
                .overlay(alignment: .bottom) { Divider() }
            }
            if let problem = store.problem {
                HStack(spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(problem).font(.callout)
                    Spacer()
                    SettingsLink { Text("Settings") }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(.regularMaterial)
                .overlay(alignment: .bottom) { Divider() }
            }
        }
    }

    /// Says what it is costing, because the count on its own reads as tidying up.
    private func strandedLine(_ harbour: Harbour) -> String {
        let n = harbour.leftBehind.count
        let jobs = n == 1 ? "One job" : "\(n) jobs"
        guard harbour.strandedBytes > 0 else {
            return "\(jobs) here belonged to a session that has gone."
        }
        return "\(jobs) here belonged to a session that has gone, and "
             + "\(Format.bytes(harbour.strandedBytes)) is still written down as promised "
             + "to them."
    }

    // MARK: - the queue

    private var sidebar: some View {
        List(selection: $selection) {
            if let harbour = store.harbour {
                if !harbour.running.isEmpty {
                    Section("Running (\(harbour.running.count))") {
                        ForEach(harbour.running) { job in
                            RunningRow(job: job).tag(Selection.running(job.id))
                        }
                    }
                }
                group("Starting now", harbour.starting)
                group("Waiting", harbour.blocked)
                group("Held by you", harbour.held)

                if !harbour.leftBehind.isEmpty {
                    Section("Left behind (\(harbour.leftBehind.count))") {
                        ForEach(harbour.leftBehind) { job in
                            AbandonedRow(job: job).tag(Selection.abandoned(job.id))
                        }
                    }
                }

                if harbour.running.isEmpty && harbour.waiting.isEmpty {
                    Section {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Nothing is queued.").font(.callout)
                            // Wrapping, not truncating: this is the one row in the
                            // sidebar that is a sentence rather than a command, and a
                            // sidebar is narrow enough to eat half of it.
                            Text(harbour.sessions.hasPrefix("one session on its own")
                                 ? "With one session open rada stands aside, and heavy "
                                 + "commands run the moment they are typed."
                                 : "Heavy commands from any session will appear here.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(nil)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .padding(.vertical, 4)
                    }
                }
            } else if store.problem == nil {
                Section { Text("Reading the queue…").foregroundStyle(.secondary) }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { footer }
    }

    @ViewBuilder
    private func group(_ title: String, _ jobs: [Waiting]) -> some View {
        if !jobs.isEmpty {
            Section("\(title) (\(jobs.count))") {
                ForEach(jobs) { job in
                    WaitingRow(job: job).tag(Selection.waiting(job.id))
                }
            }
        }
    }

    @ViewBuilder
    private var footer: some View {
        if let receipt = store.receipt, !receipt.isEmpty {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                Text(receipt).font(.caption).lineLimit(2)
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.thinMaterial)
            .overlay(alignment: .top) { Divider() }
            .task(id: receipt) {
                try? await Task.sleep(nanoseconds: 6_000_000_000)
                store.receipt = nil
            }
        }
    }

    // MARK: - the panel

    @ViewBuilder
    private var detail: some View {
        switch selection {
        case .waiting(let id):
            if let harbour = store.harbour, let job = harbour.queued(id) {
                JobDetail(job: job, harbour: harbour,
                          onForce: { forcing = job },
                          onHold: { holding = job },
                          onRelease: { Task { await store.release(job.id) } },
                          onCancelForce: { Task { await store.cancelForce(job.id) } })
            } else {
                Gone(what: "That job is no longer waiting. It either started or its "
                         + "session went away.")
            }
        case .running(let id):
            if let harbour = store.harbour, let job = harbour.started(id) {
                RunningDetail(job: job)
            } else {
                Gone(what: "That job has finished.")
            }
        case .abandoned(let id):
            if let harbour = store.harbour, let job = harbour.abandoned(id) {
                AbandonedDetail(job: job, stranded: harbour.strandedBytes,
                                onReap: { Task { await store.reap() } })
            } else {
                Gone(what: "Already let go of.")
            }
        case nil:
            if let harbour = store.harbour {
                Overview(harbour: harbour)
            } else {
                Gone(what: "Reading the queue…")
            }
        }
    }

    private var selectedWaiting: Waiting? {
        guard case .waiting(let id) = selection else { return nil }
        return store.harbour?.queued(id)
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Button {
                if let job = selectedWaiting { forcing = job }
            } label: {
                Label("Start it anyway", systemImage: "bolt.fill")
            }
            .disabled(selectedWaiting == nil)
            .help("Start this job now, ignoring the memory budget")
        }
        ToolbarItem(placement: .primaryAction) {
            Button {
                guard let job = selectedWaiting else { return }
                if job.held { Task { await store.release(job.id) } } else { holding = job }
            } label: {
                Label(selectedWaiting?.held == true ? "Release" : "Hold",
                      systemImage: selectedWaiting?.held == true
                                   ? "play.circle" : "pause.circle")
            }
            .disabled(selectedWaiting == nil)
            .help(selectedWaiting?.held == true
                  ? "Let this job back into the queue"
                  : "Keep this job from starting until you release it")
        }
    }
}

// MARK: - rows

struct RunningRow: View {
    let job: Running

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "play.circle.fill")
                .foregroundStyle(Color.accentColor)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.command)
                    .font(.system(.callout, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
        .help(job.command)
    }

    private var subtitle: String {
        var parts = [job.project.isEmpty ? "somewhere" : job.project,
                     "running \(Format.duration(job.seconds))"]
        parts.append(job.peak > 0 ? "peak \(Format.bytes(job.peak))"
                                  : "asked for \(Format.bytes(job.need))")
        return parts.joined(separator: "  ·  ")
    }
}

struct WaitingRow: View {
    let job: Waiting

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: Glyph.name(job.state))
                .foregroundStyle(Glyph.tint(job.state))
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.command)
                    .font(.system(.callout, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack(spacing: 6) {
                    Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    if job.mandatory && !job.held {
                        Tag(text: "age decides", tint: .orange)
                    }
                    if job.force != nil {
                        Tag(text: "forced", tint: .yellow)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text("\(job.position)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 2)
        .help(job.command)
    }

    private var subtitle: String {
        [job.project.isEmpty ? "somewhere" : job.project,
         "waited \(Format.duration(job.age))",
         "needs \(Format.bytes(job.need))"].joined(separator: "  ·  ")
    }
}

struct AbandonedRow: View {
    let job: LeftBehind

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "clock.badge.xmark")
                .foregroundStyle(.secondary)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.command)
                    .font(.system(.callout, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundStyle(.secondary)
                Text(subtitle).font(.caption).foregroundStyle(.tertiary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
        .help(job.command)
    }

    private var subtitle: String {
        var parts = [job.project.isEmpty ? "somewhere" : job.project,
                     "silent for \(Format.duration(job.silentFor))"]
        if job.holding > 0 { parts.append("holding \(Format.bytes(job.holding))") }
        return parts.joined(separator: "  ·  ")
    }
}

struct Tag: View {
    let text: String
    let tint: Color

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(tint.opacity(0.18), in: Capsule())
            .foregroundStyle(tint)
    }
}

enum Glyph {
    static func name(_ state: Waiting.State) -> String {
        switch state {
        case .starting: return "checkmark.circle.fill"
        case .forced:   return "bolt.circle.fill"
        case .held:     return "pause.circle.fill"
        case .stuck:    return "exclamationmark.triangle.fill"
        case .queued:   return "clock"
        }
    }

    static func tint(_ state: Waiting.State) -> Color {
        switch state {
        case .starting: return .green
        case .forced:   return .yellow
        case .held:     return .indigo
        case .stuck:    return .orange
        case .queued:   return .secondary
        }
    }

    static func headline(_ state: Waiting.State) -> String {
        switch state {
        case .starting: return "Starts as soon as it asks"
        case .forced:   return "Forced"
        case .held:     return "Held by you"
        case .stuck:    return "Cannot fit while other programs hold the memory"
        case .queued:   return "Waiting"
        }
    }
}

struct Gone: View {
    let what: String

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "water.waves")
                .font(.system(size: 34))
                .foregroundStyle(.tertiary)
            Text(what)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 380)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
