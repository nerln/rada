// Draws the rada mark at every size an .icns is packed from.
//
// The same drawing is in docs/img/mark.svg, which the site and the favicon use. It is a
// berth and the queue outside it: one bar, and two hulls waiting, the nearer one lit
// because it is the one going in. A drawing of a harbour would say nothing at sixteen
// pixels, and an anchor would say "boats" rather than "wait your turn".
//
//   swiftc -O -parse-as-library make-icon.swift -o make-icon
//   ./make-icon <output-directory>

import AppKit
import CoreGraphics
import Foundation

struct Palette {
    static let ink = CGColor(red: 0x0B / 255, green: 0x10 / 255, blue: 0x13 / 255, alpha: 1)
    static let signal = CGColor(red: 0x4F / 255, green: 0xBE / 255, blue: 0x8F / 255, alpha: 1)
    static let paper = CGColor(red: 0xE9 / 255, green: 0xEF / 255, blue: 0xEF / 255, alpha: 1)
    static let waiting = CGColor(red: 0xE9 / 255, green: 0xEF / 255, blue: 0xEF / 255, alpha: 0.42)
}

/// One tile. Coordinates are the mark's own 64 unit square, scaled up.
func draw(size: Int) -> CGImage? {
    let space = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                              bytesPerRow: 0, space: space,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return nil }

    // macOS insets its icons inside the tile rather than filling it edge to edge.
    let inset = CGFloat(size) * 0.09
    let side = CGFloat(size) - inset * 2
    let unit = side / 64.0
    func x(_ v: CGFloat) -> CGFloat { inset + v * unit }
    // Core Graphics counts from the bottom, the drawing from the top.
    func y(_ v: CGFloat) -> CGFloat { inset + (64 - v) * unit }

    ctx.setFillColor(Palette.ink)
    ctx.addPath(CGPath(roundedRect: CGRect(x: inset, y: inset, width: side, height: side),
                       cornerWidth: 14 * unit, cornerHeight: 14 * unit, transform: nil))
    ctx.fillPath()

    // The quay. Full height rather than centred: it is the edge of the water, not an
    // object in it.
    ctx.setFillColor(Palette.paper)
    ctx.addPath(CGPath(roundedRect: CGRect(x: x(45), y: y(49), width: 6 * unit,
                                           height: 34 * unit),
                       cornerWidth: 3 * unit, cornerHeight: 3 * unit, transform: nil))
    ctx.fillPath()

    // The one going in, and the one behind it.
    ctx.setFillColor(Palette.signal)
    ctx.fillEllipse(in: CGRect(x: x(34 - 5.5), y: y(32 + 5.5),
                               width: 11 * unit, height: 11 * unit))
    ctx.setFillColor(Palette.waiting)
    ctx.fillEllipse(in: CGRect(x: x(19 - 5.5), y: y(32 + 5.5),
                               width: 11 * unit, height: 11 * unit))

    return ctx.makeImage()
}

func write(_ image: CGImage, to url: URL) throws {
    let rep = NSBitmapImageRep(cgImage: image)
    guard let data = rep.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "make-icon", code: 1)
    }
    try data.write(to: url)
}

@main
struct Main {
    static func main() {
        guard CommandLine.arguments.count > 1 else {
            print("usage: make-icon <output-directory>"); exit(1)
        }
        let out = URL(fileURLWithPath: CommandLine.arguments[1])
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

        let tiles: [(String, Int)] = [
            ("icon_16x16", 16), ("icon_16x16@2x", 32),
            ("icon_32x32", 32), ("icon_32x32@2x", 64),
            ("icon_128x128", 128), ("icon_128x128@2x", 256),
            ("icon_256x256", 256), ("icon_256x256@2x", 512),
            ("icon_512x512", 512), ("icon_512x512@2x", 1024),
        ]
        for (name, size) in tiles {
            guard let image = draw(size: size) else { print("failed at \(size)"); exit(2) }
            do { try write(image, to: out.appendingPathComponent("\(name).png")) }
            catch { print("could not write \(name): \(error)"); exit(3) }
        }
        print("wrote \(tiles.count) tiles to \(out.path)")
    }
}
