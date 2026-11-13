// Where things are for this owner. Nothing here is a fixed folder of one person's Mac:
//   dataHome      the data home: JARVIS_HOME, else what build.sh wrote into the app, else ~/.jarvis
//   repoDir       the Jarvis code (the folder this app was built from; build.sh writes it into the app)
//   serverPython  the Python that runs the server (written in by build.sh; the system's otherwise)
//   port          the server's port: [server] port in the settings file, else 8765
import Foundation

let home = FileManager.default.homeDirectoryForCurrentUser.path

func builtIn(_ key: String) -> String { (Bundle.main.object(forInfoDictionaryKey: key) as? String) ?? "" }

let dataHome: String = {
    if let e = ProcessInfo.processInfo.environment["JARVIS_HOME"], !e.isEmpty { return (e as NSString).expandingTildeInPath }
    let p = builtIn("JarvisHome")
    return p.isEmpty ? "\(home)/.jarvis" : (p as NSString).expandingTildeInPath
}()

let repoDir: String = {
    if let e = ProcessInfo.processInfo.environment["JARVIS_REPO"], !e.isEmpty { return (e as NSString).expandingTildeInPath }
    return builtIn("JarvisRepo")
}()

let serverPython: String = {
    let p = builtIn("JarvisPython")
    return p.isEmpty ? "/usr/bin/python3" : p
}()

let port: UInt16 = {
    guard let t = try? String(contentsOfFile: "\(dataHome)/config.toml", encoding: .utf8), let sec = t.range(of: "[server]") else { return 8765 }
    let rest = t[sec.upperBound...]
    let body = rest.range(of: "\n[").map { rest[..<$0.lowerBound] } ?? rest
    for line in body.split(separator: "\n") {
        let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
        if parts.count == 2, parts[0] == "port", let n = UInt16(parts[1].prefix { $0.isNumber }) { return n }
    }
    return 8765
}()

/// Single quotes for a shell word, so a folder with a space or a quote in its name cannot break the command.
func shq(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }
