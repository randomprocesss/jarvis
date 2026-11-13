// Text for the ear, applied just before TTS (settings.json "lexicon"): the pronunciation lexicon (voice/lexicon.json), interjections
// the voices would spell out, ids, money, percentages, times, dates, ranges, ordinals and numbers as words.
// Pure Foundation, so it is unit-tested on its own (mac/tests/SpeechTextTests.swift).
import Foundation

enum SpeechText {
    static let gb = Locale(identifier: "en_GB")
    static let spell: NumberFormatter = { let f = NumberFormatter(); f.locale = gb; f.numberStyle = .spellOut; return f }()

    /// Cardinal words, British style: "one hundred and fifty-eight", "two thousand and five".
    static func words(_ n: Int) -> String {
        var w = spell.string(from: NSNumber(value: n)) ?? String(n)
        w = w.replacingOccurrences(of: #"hundred (?!thousand|million|billion)(?=\w)"#, with: "hundred and ", options: .regularExpression)
        let r = abs(n) % 1000
        if abs(n) >= 1000 && r > 0 && r < 100, let x = w.range(of: #"(thousand|million|billion) (?=[\w-]+$)"#, options: .regularExpression) {
            w.replaceSubrange(x, with: String(w[x]) + "and ")
        }
        return w
    }

    /// "twenty-four" -> "twenty-fourth"
    static func ordinal(_ n: Int) -> String {
        let w = words(n)
        var parts = w.components(separatedBy: CharacterSet(charactersIn: " -"))
        let seps = w.filter { $0 == " " || $0 == "-" }.map(String.init)
        guard let last = parts.popLast() else { return w }
        let irregular = ["one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"]
        let o = irregular[last] ?? (last.hasSuffix("y") ? String(last.dropLast()) + "ieth" : last + "th")
        var out = ""
        for (i, p) in parts.enumerated() { out += p + (i < seps.count ? seps[i] : " ") }
        return out + o
    }

    /// Digits one by one, zero as "oh": 505 -> "five oh five"
    static func digits(_ s: String) -> String {
        let names = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        return s.compactMap { $0.wholeNumberValue.map { names[$0] } }.joined(separator: " ")
    }

    /// 2026 -> "twenty twenty-six", 2000 -> "two thousand", 2005 -> "two thousand and five"
    static func year(_ y: Int) -> String {
        if y >= 2000 && y < 2010 { return words(y) }
        if y >= 1100 && y < 2100 {
            let hi = y / 100, lo = y % 100
            return words(hi) + " " + (lo == 0 ? "hundred" : lo < 10 ? "oh " + words(lo) : words(lo))
        }
        return words(y)
    }

    static func decimal(_ s: String) -> String {
        let p = s.replacingOccurrences(of: ",", with: "").split(separator: ".", maxSplits: 1).map(String.init)
        guard let i = Int(p[0]) else { return s }
        if p.count < 2 || p[1].isEmpty { return words(i) }
        return words(i) + " point " + digits(p[1]).replacingOccurrences(of: "oh", with: "zero")
    }

    static let months = ["jan": "January", "feb": "February", "mar": "March", "apr": "April", "may": "May", "jun": "June", "jul": "July",
                         "aug": "August", "sep": "September", "sept": "September", "oct": "October", "nov": "November", "dec": "December"]
    static let monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

    /// 14:30 -> "half past two", 09:15 -> "quarter past nine", 14:45 -> "quarter to three", 14:00 -> "two o'clock", 14:37 -> "two thirty-seven"
    static func clock(_ h24: Int, _ m: Int) -> String {
        let h = h24 % 12 == 0 ? 12 : h24 % 12, next = (h24 + 1) % 12 == 0 ? 12 : (h24 + 1) % 12
        switch m {
        case 0: return words(h) + " o'clock"
        case 15: return "quarter past " + words(h)
        case 30: return "half past " + words(h)
        case 45: return "quarter to " + words(next)
        case 5, 10, 20, 25: return words(m) + " past " + words(h)
        case 35, 40, 50, 55: return words(60 - m) + " to " + words(next)
        default: return words(h) + " " + (m < 10 ? "oh " + words(m) : words(m))
        }
    }

    /// Replace every match of `pattern` with the closure's result (groups passed in; nil keeps the match).
    static func sub(_ s: String, _ pattern: String, _ opts: NSRegularExpression.Options = [], _ f: ([String]) -> String?) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern, options: opts) else { return s }
        let ns = s as NSString
        var out = "", last = 0
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            var g: [String] = []
            for i in 0..<m.numberOfRanges { let r = m.range(at: i); g.append(r.location == NSNotFound ? "" : ns.substring(with: r)) }
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            out += f(g) ?? g[0]
            last = m.range.location + m.range.length
        }
        return out + ns.substring(from: last)
    }

    static func escape(_ s: String) -> String { NSRegularExpression.escapedPattern(for: s) }

    /// The whole pass. `lexicon`: word -> how to say it (all-caps keys match case-sensitively, others in any case).
    static func normalize(_ input: String, lexicon: [String: String] = [:]) -> String {
        var s = input
        // interjections the voices spell out ("M M"): only what Jarvis says; the backchannel filter (what he says) is separate
        // (checked against Kokoro's phonemes: "Mm", "Mhm" and "Mm-hmm" come out as letters; "Hmm" and "Uh huh" as sounds)
        s = sub(s, #"(?<![\w-])[Mm](?:m*-?h+m+|h+m*)(?![\w-])"#) { g in g[0].first == "M" ? "Uh huh" : "uh huh" }
        s = sub(s, #"(?<![\w-])[Mm]m+(?![\w-])"#) { g in g[0].first == "M" ? "Hmm" : "hmm" }
        s = sub(s, #"(?<![\w-])([Uu])h-huh(?![\w-])"#) { g in g[1] + "h huh" }
        // the lexicon, longest keys first (so "ABC-505" wins over "ABC")
        for k in lexicon.keys.sorted(by: { $0.count > $1.count }) {
            guard let v = lexicon[k], !k.isEmpty else { continue }
            let caps = k == k.uppercased()
            s = sub(s, "(?<![\\w-])" + escape(k) + "(?![\\w-])", caps ? [] : [.caseInsensitive]) { _ in v }
        }
        s = s.replacingOccurrences(of: "&", with: " and ")
        s = sub(s, #"\be\.g\.,?"#, [.caseInsensitive]) { _ in "for example" }
        s = sub(s, #"\bi\.e\.,?"#, [.caseInsensitive]) { _ in "that is" }
        s = sub(s, #"\bvs\.?(?=\s)"#, [.caseInsensitive]) { _ in "versus" }
        s = sub(s, #"\bapprox\.?(?=\s)"#, [.caseInsensitive]) { _ in "about" }
        // ids like ABC-505 or XY-1234: letters spelled, digits one by one
        s = sub(s, #"\b([A-Z]{2,6})-(\d{1,6})\b"#) { g in g[1].map(String.init).joined(separator: " ") + " " + digits(g[2]) }
        // money: $12.50, $5.7k, $1,234, $2m
        s = sub(s, #"\$(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s?(k|m|bn|K|M)?\b"#) { g in
            let whole = Int(g[1].replacingOccurrences(of: ",", with: "")) ?? 0
            if !g[3].isEmpty {
                let unit = ["k": "thousand", "K": "thousand", "m": "million", "M": "million", "bn": "billion"][g[3]] ?? ""
                let num = g[2].isEmpty ? words(whole) : decimal(g[1] + "." + g[2])
                return num + " " + unit + " dollars"
            }
            var out = words(whole) + (whole == 1 ? " dollar" : " dollars")
            if !g[2].isEmpty, let c = Int(g[2].count == 1 ? g[2] + "0" : g[2]), c > 0 { out += " and " + words(c) + (c == 1 ? " cent" : " cents") }
            return out
        }
        // percentages: 56.9% -> fifty-six point nine percent
        s = sub(s, #"(\d+(?:\.\d+)?)\s?%"#) { g in decimal(g[1]) + " percent" }
        // times: 14:30, 2:30 pm, 9am
        s = sub(s, #"\b([01]?\d|2[0-3]):([0-5]\d)(?:\s?([ap])\.?m\.?)?(?![\d:])"#, [.caseInsensitive]) { g in
            guard var h = Int(g[1]), let m = Int(g[2]) else { return nil }
            if g[3].lowercased() == "p" && h < 12 { h += 12 }
            return clock(h, m)
        }
        s = sub(s, #"\b(1[0-2]|[1-9])\s?([ap])\.?m\.?(?![\w])"#, [.caseInsensitive]) { g in
            guard let h = Int(g[1]) else { return nil }
            return words(h) + (g[2].lowercased() == "a" ? " in the morning" : h >= 6 ? " in the evening" : " in the afternoon")
        }
        // dates: 2026-10-02, 2 Oct (2026), Oct 2(nd) (2026)
        s = sub(s, #"\b(\d{4})-(\d{2})-(\d{2})\b"#) { g in
            guard let y = Int(g[1]), let mo = Int(g[2]), let d = Int(g[3]), (1...12).contains(mo), (1...31).contains(d) else { return nil }
            return "the " + ordinal(d) + " of " + monthNames[mo - 1] + ", " + year(y)
        }
        let mon = "(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)[a-z]*\\.?"
        s = sub(s, "\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+" + mon + "(?:,?\\s+(\\d{4}))?\\b", [.caseInsensitive]) { g in
            guard let d = Int(g[1]), (1...31).contains(d), let m = months[g[2].lowercased()] else { return nil }
            return "the " + ordinal(d) + " of " + m + (g[3].isEmpty ? "" : ", " + year(Int(g[3]) ?? 0))
        }
        s = sub(s, "\\b" + mon + "\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:,?\\s+(\\d{4}))?\\b", [.caseInsensitive]) { g in
            guard let d = Int(g[2]), (1...31).contains(d), let m = months[g[1].lowercased()] else { return nil }
            return m + " " + ordinal(d) + (g[3].isEmpty ? "" : ", " + year(Int(g[3]) ?? 0))
        }
        // ranges: 10–15, 10-15 -> ten to fifteen
        s = sub(s, #"\b(\d+(?:\.\d+)?)\s?[–—-]\s?(\d+(?:\.\d+)?)\b"#) { g in decimal(g[1]) + " to " + decimal(g[2]) }
        // ordinals: 24th -> twenty-fourth
        s = sub(s, #"\b(\d+)(st|nd|rd|th)\b"#) { g in Int(g[1]).map { ordinal($0) } }
        // years on their own (1900-2099), then every other number
        s = sub(s, #"(?<![\d.,])(19\d\d|20\d\d)(?![\d.,%])"#) { g in Int(g[1]).map { year($0) } }
        s = sub(s, #"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\w])"#) { g in
            if g[2].isEmpty { return Int(g[1].replacingOccurrences(of: ",", with: "")).map { words($0) } }
            return decimal(g[1] + g[2])
        }
        s = sub(s, #"\s{2,}"#) { _ in " " }
        return s.trimmingCharacters(in: .whitespaces)
    }

    // MARK: lexicon file

    /// voice/lexicon.json: {"words": {"Q3": "Q three", ...}}
    static func loadLexicon(_ path: String) -> [String: String] {
        guard let d = FileManager.default.contents(atPath: path),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any], let w = o["words"] as? [String: String] else { return [:] }
        return w
    }

    /// Adds or changes one word (keeps the rest of the file). Returns false on a bad word or a write error.
    static func addToLexicon(_ path: String, word: String, say: String) -> Bool {
        let w = word.trimmingCharacters(in: .whitespacesAndNewlines), v = say.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !w.isEmpty, w.count <= 40, !v.isEmpty, v.count <= 80, !w.contains("\n"), !v.contains("\n") else { return false }
        var o: [String: Any] = [:]
        if let d = FileManager.default.contents(atPath: path), let x = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] { o = x }
        var words = (o["words"] as? [String: String]) ?? [:]
        words[w] = v
        o["words"] = words
        guard let d = try? JSONSerialization.data(withJSONObject: o, options: [.prettyPrinted, .sortedKeys]) else { return false }
        return (try? d.write(to: URL(fileURLWithPath: path), options: .atomic)) != nil
    }
}
