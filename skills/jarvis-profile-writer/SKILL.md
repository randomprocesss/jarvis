---
name: jarvis-profile-writer
description: Writer profile for a Jarvis task whose result is text for other people (a reply, a review of a document, a write-up, a page, release notes). Used together with jarvis-inbox-work when Jarvis's router picks "writer". It never removes any tool or step.
---
# Writer profile

1. **Their voice:** the note for this person first (`curl -s 'http://localhost:{{port}}/api/recipient_note?name=<first name>'`,
   learned from {{owner}}'s edits to earlier drafts), then `{{home}}/style.md`. Answer first, then detail. Short
   sentences, their own register, no corporate filler.
2. **One message per person per topic:** if the same person asked the same thing in two places, answer once, where the
   conversation is (jarvis-inbox-work §4).
3. **Every claim checks out:** each number or fact in the text is in the outputs, the data, or the source document.
   "The guide says X" means you read it. A true sentence must not imply something false.
4. **Pages and documents others will keep:** when {{owner}}'s memory notes hold rules for how such a page is written
   (layout, what never goes on one, what a reviewer asked for before), read and follow them before writing.
5. **Long content:** goes in a write-up file (`.md`); the reply carries a short summary (jarvis-inbox-work attachment rules).
[[if no_ai_words]]6. Shared text never mentions Claude, Jarvis or AI.
[[end]]7. **Checks are robust:** before a draft is saved, every claim in it is re-checked independently from the primary
   source (jarvis-inbox-work §3f), not from your notes or another agent's verdict. A claim you cannot check twice is cut
   or marked as "likely".

**Where the procedure lives** (jarvis-inbox-work keeps it whole; read those sections first): §1 (their style), §4 (draft
the reply; attachments), §4a (every task ends in one clear state), §3c (questions and tasks), §5 (the work file).
