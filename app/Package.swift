// swift-tools-version:5.9
import PackageDescription
let package = Package(name: "Review", platforms: [.macOS(.v14)], targets: [.target(name: "LiveTipsCore"), .executableTarget(name: "CoreChecks", dependencies: ["LiveTipsCore"])])
