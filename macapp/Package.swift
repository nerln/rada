// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Rada",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "Rada",
            path: "Sources/Rada",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        // What is worth testing here is the reading, not the drawing: the window is a
        // renderer for a JSON document produced by another program, and every bug it
        // has had so far was a field that changed shape or a number formatted a
        // different way from the one the terminal prints.
        .testTarget(
            name: "RadaTests",
            dependencies: ["Rada"],
            path: "Tests/RadaTests",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
