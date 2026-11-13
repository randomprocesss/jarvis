// swiftc ../SpeechText.swift main.swift -o <scratch>/stt && <scratch>/stt
import Foundation
let lex = ["Q3X": "Q three X", "QA": "Q A", "ABC-505": "A B C five oh five", "ORDERLOG": "order log", "ERP": "E R P", "CRM": "C R M", "Saoirse": "Seer-sha"]
let cases: [(String, String)] = [
    ("Mm, one sec.", "Hmm, one sec."), ("mm okay", "hmm okay"), ("Mmm.", "Hmm."), ("Mhm, got it.", "Uh huh, got it."), ("Uh-huh.", "Uh huh."),
    ("Mm-hmm, sure.", "Uh huh, sure."), ("mmhmm", "uh huh"), ("Hmm.", "Hmm."), ("Mmm, right", "Hmm, right"), ("Hmm, one second.", "Hmm, one second."),
    ("Q3X is in QA.", "Q three X is in Q A."), ("Check ABC-505 today.", "Check A B C five oh five today."), ("ABC-653 is done", "A B C six five three is done"),
    ("Ask Saoirse about ORDERLOG in ERP.", "Ask Seer-sha about order log in E R P."), ("the CRM table", "the C R M table"),
    ("It costs $12.07.", "It costs twelve dollars and seven cents."), ("$5.7k saved", "five point seven thousand dollars saved"), ("$1,234", "one thousand two hundred and thirty-four dollars"), ("$1", "one dollar"),
    ("Fill went from 56.9% to 82.6%.", "Fill went from fifty-six point nine percent to eighty-two point six percent."),
    ("at 14:30", "at half past two"), ("at 09:15", "at quarter past nine"), ("at 14:45", "at quarter to three"), ("by 14:00", "by two o'clock"), ("at 14:37", "at two thirty-seven"), ("at 12:30", "at half past twelve"), ("at 2:30 pm", "at half past two"), ("at 9am", "at nine in the morning"),
    ("On 2 Oct we ship.", "On the second of October we ship."), ("Oct 2", "October second"), ("2 October 2026", "the second of October, twenty twenty-six"), ("2026-10-02", "the second of October, twenty twenty-six"), ("September 24th", "September twenty-fourth"),
    ("10-15 items", "ten to fifteen items"), ("10–15", "ten to fifteen"), ("164 recs", "one hundred and sixty-four recs"), ("48,213 rows", "forty-eight thousand two hundred and thirteen rows"), ("2.45 million", "two point four five million"),
    ("in 2026", "in twenty twenty-six"), ("2005", "two thousand and five"), ("1005 rows", "one thousand and five rows"), ("200000", "two hundred thousand"), ("2,000,300", "two million three hundred"), ("the 21st", "the twenty-first"), ("e.g. this", "for example this"), ("Q&A", "Q and A"),
]
var fails = 0
for (i, o) in cases {
    let got = SpeechText.normalize(i, lexicon: lex)
    if got != o { fails += 1; print("FAIL  \(i)  ->  \(got)   (want \(o))") }
}
print("\(cases.count - fails)/\(cases.count) passed")
exit(fails == 0 ? 0 : 1)
