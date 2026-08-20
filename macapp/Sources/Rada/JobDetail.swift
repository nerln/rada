import SwiftUI

/// One job's reasons, and the two decisions a person can take about it.
///
/// Every sentence under "Why" is the one rada wrote for that job. None of it is
/// rephrased here: when somebody reads the same thing in a terminal an hour later it
/// has to be word for word the same, or one of the two is lying.
struct JobDetail: View {
    let job: Waiting
    let harbour: Harbour
    let onForce: () -> Void
    let onHold: () -> Void
    let onRelease: () -> Void
    let onCancelForce: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                heading
                reason
                facts
                if !job.blockers.isEmpty { blockers }
                judgeVerdict
                controls
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .topLeading)
        }
        // Recent macOS fades the first few points of a scrolling view against the
        // window edge. Without this the heading of every panel came out half erased.
        .contentMargins(.top, 22, for: .scrollContent)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label {
                Text(Glyph.headline(job.state)).font(.headline)
            } icon: {
                Image(systemName: Glyph.name(job.state))
                    .foregroundStyle(Glyph.tint(job.state))
            }
            Text(job.command)
                .font(.system(.title3, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 14) {
                Label(job.project.isEmpty ? "no project" : job.project,
                      systemImage: "folder")
                Label("ticket \(job.id)", systemImage: "number")
                // The time of day it was queued, not only how long ago. Four jobs from
                // four sessions look alike, and the clock is what a person matches
                // against their own scrollback to work out which one this is.
                Label("queued at \(Format.time(job.queuedAt))", systemImage: "clock")
            }
            .font(.callout)
            .foregroundStyle(.secondary)
            if !job.cwd.isEmpty {
                Text(job.cwd)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .textSelection(.enabled)
                    .lineLimit(1)
                    .truncationMode(.head)
            }
        }
    }

    private var reason: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Why").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            Text(job.why)
                .font(.system(size: 15))
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if !job.note.isEmpty {
                Divider().padding(.vertical, 2)
                Label {
                    Text("The session that queued it said: \(job.note)")
                        .fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "text.quote")
                }
                .font(.callout)
                .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Glyph.tint(job.state).opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(Glyph.tint(job.state).opacity(0.55))
                .frame(width: 3)
                .clipShape(RoundedRectangle(cornerRadius: 2))
        }
    }

    private var facts: some View {
        Grid(alignment: .leading, horizontalSpacing: 26, verticalSpacing: 9) {
            row("Place in the queue",
                job.held ? "held, so it is not competing for a berth"
                         : "\(job.position) of \(harbour.waiting.count)"
                           + (job.mandatory ? ", and old enough that age alone decides" : ""))
            row("Queued by", job.session.isEmpty
                ? "a session that did not say which"
                : "session \(job.session.prefix(8)), \(Format.duration(job.age)) ago")
            row("Memory it wants", Format.bytes(job.need)
                + (job.declared ? ", declared with --need" : ", learned from earlier runs"))
            row("Free right now", Format.bytes(harbour.memory.free)
                + " of a \(Format.bytes(harbour.memory.budget)) budget")
            if job.judgeBonus > 0 {
                row("The judge moved it up",
                    String(format: "%.1f points, worth %.0f seconds of waiting",
                           job.judgeBonus, job.judgeBonus * 30))
            }
            if let hold = job.hold {
                row("Held", "since \(Format.duration(harbour.now - hold.since)) ago"
                    + ((hold.note?.isEmpty == false) ? ", \(hold.note!)" : ""))
            }
            if let force = job.force {
                row("Forced", forceLine(force))
            }
        }
        .font(.callout)
    }

    private func row(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.leading)
            Text(value).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func forceLine(_ force: Force) -> String {
        if let after = force.after { return "starts once \(after) has finished" }
        guard let at = force.at else { return "starts now" }
        let left = at - harbour.now
        return left <= 0 ? "cleared to start, ignoring the budget"
                         : "starts in \(Format.duration(left)), ignoring the budget"
    }

    private var blockers: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Who is holding the memory")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(job.blockers) { blocker in
                HStack(spacing: 10) {
                    Image(systemName: "app.dashed").foregroundStyle(.tertiary)
                    Text(blocker.name).lineLimit(1)
                    Spacer()
                    Text(Format.bytes(blocker.bytes))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                .font(.callout)
            }
            Text("These are not queued jobs. Nothing rada does will free them, which is "
                 + "why this one waits without holding the queue shut behind it.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var judgeVerdict: some View {
        if harbour.judge.hasVerdict, let rank = harbour.judge.order.firstIndex(of: job.id) {
            VStack(alignment: .leading, spacing: 6) {
                Text("The judge put it \(rank + 1) of \(harbour.judge.order.count)")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(harbour.judge.why.isEmpty ? "It gave no reason." : harbour.judge.why)
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                Text("A verdict is worth at most three points against one point for "
                     + "every thirty seconds of waiting, so it can reorder recent "
                     + "arrivals and nothing older than ninety seconds.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider()
            HStack(spacing: 12) {
                Button(action: onForce) {
                    Label(job.force == nil ? "Start it anyway" : "Change when it starts",
                          systemImage: "bolt.fill")
                }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)

                if job.held {
                    Button(action: onRelease) {
                        Label("Release it", systemImage: "play.circle")
                    }
                    .controlSize(.large)
                } else {
                    Button(action: onHold) {
                        Label("Hold it", systemImage: "pause.circle")
                    }
                    .controlSize(.large)
                }

                if job.force != nil {
                    Button("Cancel the force", action: onCancelForce)
                        .controlSize(.large)
                }
                Spacer()
            }
            Text(job.held
                 ? "Released, it goes back to the place its arrival time earns. Holding "
                   + "it did not cost it its turn."
                 : "Forcing starts it past the budget, which is a decision rada will not "
                   + "take on its own. Holding keeps it out of the running without "
                   + "taking it out of the queue.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// A job that is already running. There is nothing to decide here: rada never stops
/// anything a person started, so this panel only says what it is and what it is costing.
struct RunningDetail: View {
    let job: Running

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Label {
                    Text("Running for \(Format.duration(job.seconds))").font(.headline)
                } icon: {
                    Image(systemName: "play.circle.fill").foregroundStyle(Color.accentColor)
                }
                Text(job.command)
                    .font(.system(.title3, design: .monospaced))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Grid(alignment: .leading, horizontalSpacing: 26, verticalSpacing: 9) {
                    GridRow {
                        Text("Project").foregroundStyle(.secondary)
                        Text(job.project.isEmpty ? "no project" : job.project)
                    }
                    GridRow {
                        Text("Started").foregroundStyle(.secondary)
                        Text(Format.time(job.startedAt))
                    }
                    GridRow {
                        Text("Berth it was given").foregroundStyle(.secondary)
                        Text(Format.bytes(job.need))
                    }
                    GridRow {
                        Text("Largest footprint so far").foregroundStyle(.secondary)
                        Text(job.peak > 0 ? Format.bytes(job.peak)
                                          : "not sampled yet")
                    }
                    if let pid = job.pid {
                        GridRow {
                            Text("Process").foregroundStyle(.secondary)
                            Text(String(pid)).monospacedDigit()
                        }
                    }
                }
                .font(.callout)
                Text("rada does not stop what it started. What this job really used is "
                     + "measured while it runs and remembered, so the next command like "
                     + "it is queued against a number rather than a guess.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(28)
            .frame(maxWidth: 700, alignment: .topLeading)
        }
        .contentMargins(.top, 22, for: .scrollContent)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// A job whose process has gone.
///
/// This panel exists because of the one report that mattered: a queue that looked busy
/// with jobs nobody recognised, and no way to tell the live ones from the leftovers. A
/// berth is written down when a job starts and given back when it ends, and a session
/// that is closed mid-job never gives it back. Every waiter sweeps these away before it
/// decides anything, so they last exactly as long as the machine stays quiet.
struct AbandonedDetail: View {
    let job: LeftBehind
    let stranded: Int
    let onReap: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Label {
                    Text(job.wasRunning ? "This job was running when its session went away"
                                        : "This job was waiting when its session went away")
                        .font(.headline)
                } icon: {
                    Image(systemName: "clock.badge.xmark").foregroundStyle(.secondary)
                }
                Text(job.command)
                    .font(.system(.title3, design: .monospaced))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Grid(alignment: .leading, horizontalSpacing: 26, verticalSpacing: 9) {
                    GridRow {
                        Text("Project").foregroundStyle(.secondary)
                        Text(job.project.isEmpty ? "no project" : job.project)
                    }
                    GridRow {
                        Text("Nothing heard for").foregroundStyle(.secondary)
                        Text(Format.duration(job.silentFor))
                    }
                    if let pid = job.pid {
                        GridRow {
                            Text("Its process").foregroundStyle(.secondary)
                            Text("\(String(pid)), which is not running").monospacedDigit()
                        }
                    }
                    if job.holding > 0 {
                        GridRow {
                            Text("Still written down as").foregroundStyle(.secondary)
                            Text("\(Format.bytes(job.holding)) promised to it")
                        }
                    }
                }
                .font(.callout)
                Text(job.holding > 0
                     ? "Until it is let go of, that memory is counted against the budget "
                     + "and jobs behind it wait for room that nothing is using. The "
                     + "numbers above the queue are already shown without it."
                     : "It is out of the queue already. Letting go of it only takes the "
                     + "line off the screen.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Divider()
                Button(action: onReap) {
                    Label(stranded > 0
                          ? "Let go of it and free \(Format.bytes(stranded))"
                          : "Let go of it", systemImage: "wind")
                }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)
                Text("The same as rada reap on the command line. It drops every job "
                     + "whose process is gone and touches nothing that is running.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(28)
            .frame(maxWidth: 700, alignment: .topLeading)
        }
        .contentMargins(.top, 22, for: .scrollContent)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// What the whole queue is about to do, for somebody who has just looked at the window.
struct Overview: View {
    let harbour: Harbour

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(headline).font(.title2).bold()
                        .fixedSize(horizontal: false, vertical: true)
                    Text(harbour.sessions).font(.callout).foregroundStyle(.secondary)
                }

                if !harbour.starting.isEmpty {
                    section("Starting as soon as they ask", harbour.starting, tint: .green)
                }
                if let first = harbour.blocked.first {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("First in the queue that does not go")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(first.command)
                            .font(.system(.callout, design: .monospaced))
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Text(first.why)
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !harbour.held.isEmpty {
                    section("Held by you, and not competing for anything",
                            harbour.held, tint: .indigo)
                }

                if harbour.judge.hasVerdict {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(judgeAge).font(.subheadline.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(harbour.judge.why)
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                Divider()
                Text("Pick a job on the left to see what it is waiting for, who is "
                     + "holding the memory it needs, and to start it past the budget or "
                     + "keep it out until you say otherwise.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(28)
            .frame(maxWidth: 720, alignment: .topLeading)
        }
        .contentMargins(.top, 22, for: .scrollContent)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var headline: String {
        let waiting = harbour.waiting.count
        if waiting == 0 && harbour.running.isEmpty {
            return "Nothing is queued."
        }
        if waiting == 0 {
            return harbour.running.count == 1
                ? "One job is running and nothing is waiting."
                : "\(harbour.running.count) jobs are running and nothing is waiting."
        }
        let go = harbour.starting.count
        let one = waiting == 1
        if go == 0 {
            return one ? "The one job in the queue is waiting."
                       : "None of the \(waiting) jobs in the queue starts right now."
        }
        return go == 1
            ? "1 of \(waiting) jobs in the queue starts as soon as it asks."
            : "\(go) of \(waiting) jobs in the queue start as soon as they ask."
    }

    private var judgeAge: String {
        guard let age = harbour.judge.age else { return "The judge" }
        return "The judge, \(Format.duration(age)) ago"
    }

    private func section(_ title: String, _ jobs: [Waiting], tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            ForEach(jobs) { job in
                HStack(spacing: 9) {
                    Image(systemName: Glyph.name(job.state)).foregroundStyle(tint)
                    Text(job.command)
                        .font(.system(.callout, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Text(Format.bytes(job.need))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

// MARK: - the two decisions, asked properly

/// Forcing has three shapes, and the difference between them matters enough to be a
/// panel rather than three buttons in a row: now, in a moment, or behind a job that is
/// already running.
struct ForceSheet: View {
    let job: Waiting
    let running: [Running]
    let confirm: (Double?, String?) -> Void
    @Environment(\.dismiss) private var dismiss

    enum When: Hashable { case now, delay, after }

    @State private var when: When = .now
    @State private var delay: Double = 60
    @State private var after: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Start this job past the budget").font(.headline)
                Text(job.command)
                    .font(.system(.callout, design: .monospaced))
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .foregroundStyle(.secondary)
                if job.held {
                    Label("It is held. Starting it lifts the hold.",
                          systemImage: "pause.circle")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.top, 2)
                }
            }

            Picker("", selection: $when) {
                Text("Now").tag(When.now)
                Text("In a moment").tag(When.delay)
                Text("After a running job").tag(When.after)
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            switch when {
            case .now:
                Text("It stops waiting for room and starts. The machine may go into "
                     + "swap: rada refuses this on its own, and you are saying you know "
                     + "something it does not.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            case .delay:
                VStack(alignment: .leading, spacing: 8) {
                    Picker("Wait first", selection: $delay) {
                        Text("30 seconds").tag(30.0)
                        Text("1 minute").tag(60.0)
                        Text("5 minutes").tag(300.0)
                        Text("15 minutes").tag(900.0)
                    }
                    Text("Long enough to close what is holding the memory before it goes.")
                        .font(.callout).foregroundStyle(.secondary)
                }
            case .after:
                VStack(alignment: .leading, spacing: 8) {
                    if running.isEmpty {
                        Text("Nothing is running for it to wait for.")
                            .font(.callout).foregroundStyle(.secondary)
                    } else {
                        Picker("Once this has finished", selection: $after) {
                            Text("Pick one").tag("")
                            ForEach(running) { job in
                                Text("\(job.id)  \(job.command)").tag(job.id)
                            }
                        }
                        Text("It goes the moment that job finishes, whatever the budget "
                             + "says then.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Start it") {
                    switch when {
                    case .now:   confirm(nil, nil)
                    case .delay: confirm(delay, nil)
                    case .after: confirm(nil, after.isEmpty ? nil : after)
                    }
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                .disabled(when == .after && after.isEmpty)
            }
        }
        .padding(22)
        .frame(width: 480)
    }
}

/// Holding asks for one line, because a hold with no reason on it is a job nobody
/// remembers holding, found three hours later at the bottom of the queue.
struct HoldSheet: View {
    let job: Waiting
    let confirm: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var note = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Keep this job from starting").font(.headline)
                Text(job.command)
                    .font(.system(.callout, design: .monospaced))
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .foregroundStyle(.secondary)
            }
            TextField("Why, in a few words", text: $note)
                .textFieldStyle(.roundedBorder)
                .onSubmit { commit() }
            Text("It keeps its place and keeps ageing, so releasing it does not send it "
                 + "to the back. Until then it takes no memory reservation and nothing "
                 + "waits behind it.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Hold it") { commit() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(22)
        .frame(width: 460)
    }

    private func commit() {
        confirm(note)
        dismiss()
    }
}
