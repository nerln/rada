import SwiftUI

/// The context every decision on this screen is made against.
///
/// It sits above the queue rather than beside a job, because what decides whether
/// anything starts is a property of the machine and not of any job in the list. Three
/// quantities, in the order somebody reads them: what is left for the next job, what is
/// already promised to the ones running, and what the whole budget was. Then the machine
/// in its own words, then who else is here, and last the sentence that matters most on an
/// empty queue: whether rada is about to hold the next job back at all.
struct HarbourBar: View {
    let memory: Memory
    let sessions: String
    let aside: StandingAside
    let reservation: Reservation
    let now: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 18) {
                figure(Format.bytes(memory.free), "free for the next job",
                       tint: memory.free > 0 ? .green : .orange)
                figure(Format.bytes(memory.promised), "promised to running jobs")
                figure(Format.bytes(memory.budget), "budget")
                Spacer(minLength: 12)
                VStack(alignment: .trailing, spacing: 2) {
                    Text("\(Format.bytes(memory.used)) of \(Format.bytes(memory.total)) in use")
                    Text(swapLine)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            meter

            HStack(spacing: 8) {
                Image(systemName: "sailboat")
                    .imageScale(.small)
                    .foregroundStyle(.secondary)
                Text(sessions).font(.caption).foregroundStyle(.secondary)
                if let note = reservationLine {
                    Text("·").font(.caption).foregroundStyle(.tertiary)
                    Text(note).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }

            // Whether rada is about to hold anything back. Somebody looking at an empty
            // queue is asking exactly this, and no list on the screen answers it.
            HStack(spacing: 8) {
                Image(systemName: aside.on ? "checkmark.circle.fill" : "arrow.triangle.branch")
                    .imageScale(.small)
                    .foregroundStyle(aside.on ? .green : .secondary)
                Text(aside.why)
                    .font(.caption)
                    .foregroundStyle(aside.on ? .primary : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
            }

            // rada's own words for why the budget is not what the free memory suggests.
            // A queue that stops admitting anything while the machine looks half empty
            // is the moment somebody decides the tool is broken.
            ForEach(memory.clamped, id: \.self) { reason in
                Label(reason, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            if memory.unknownPlatform {
                Label("Memory cannot be read on this machine, so nothing is being held back.",
                      systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.regularMaterial)
        .overlay(alignment: .bottom) { Divider() }
    }

    private func figure(_ value: String, _ caption: String,
                        tint: Color = .primary) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.system(.title3, design: .rounded).weight(.semibold))
                .foregroundStyle(tint)
                .monospacedDigit()
            Text(caption).font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }

    /// Promised and free, drawn to scale against the budget. Widths are clamped to a
    /// visible minimum: a sliver of a pixel says "none left" when there is some.
    private var meter: some View {
        GeometryReader { geo in
            let width = geo.size.width
            let total = Double(max(memory.budget, memory.promised + memory.free, 1))
            let promised = width * min(1, Double(memory.promised) / total)
            let free = width * min(1, Double(memory.free) / total)
            HStack(spacing: 2) {
                Capsule().fill(Color.accentColor.opacity(0.75))
                    .frame(width: memory.promised > 0 ? max(6, promised) : 0)
                Capsule().fill(Color.green.opacity(0.55))
                    .frame(width: memory.free > 0 ? max(6, free) : 0)
                Capsule().fill(Color.secondary.opacity(0.18))
            }
        }
        .frame(height: 7)
        .accessibilityLabel("\(Format.bytes(memory.free)) free of a "
                            + "\(Format.bytes(memory.budget)) budget")
    }

    private var swapLine: String {
        let pressure = memory.pressure == 1 ? "pressure normal"
                                            : "pressure \(memory.pressure)"
        guard memory.swapTotal > 0 else { return pressure }
        return "\(pressure) · swap \(Format.bytes(memory.swapUsed)) "
             + "of \(Format.bytes(memory.swapTotal))"
    }

    private var reservationLine: String? {
        if let id = reservation.id {
            return "holding the machine open for \(id)"
        }
        if let until = reservation.cooldownUntil, until > now {
            return "reservation on cooldown for \(Format.duration(until - now))"
        }
        return nil
    }
}
