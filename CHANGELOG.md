# Changes

Each release is a git tag `vX.Y.Z` with the same number as the `VERSION` file. The app offers a release only when its
tag is newer than the copy you run, and only updates on your press.

## Unreleased

Found by installing 0.1.0 as a new person would, on a fresh home folder:

- Sends, approvals and writes were refused on any port but 8765, the Mac app's window included: the page's address
  now follows the port Jarvis really listens on.
- Round-ups and scans no longer start before Setup is far enough: Claude must have answered its check, and a round-up
  also waits for a connected app. Before, the first one started a minute after install and failed.
- Home and Today say what Setup still needs instead of "Everything is in place"; Home's button leads to Setup.
- The ticket screen, its count and its tiles appear only once a ticket system is connected (or tickets exist).
- Updates: when the release check cannot reach the repository, Setup says so instead of hiding the section.
- The demo stays one press away on Setup after your name is saved; `jarvis stop` stops a demo left open, and only a demo.
- Spoken lines open with what you asked to be called, never a stock "sir"; a notice shows once in a browser, not twice.
- Smaller: the send dialog names a real address only; the terminal says "run `jarvis doctor` again" (it has no Check
  button); install.sh says how to put `~/.local/bin` on the PATH; the Brain hint has no domain words.
- The docs now say plainly that sending and approving need the Mac app for now.

## 0.1.0 (14 Nov 2026)

The first release for other people to install.

- Setup in the app: a new install opens on Setup; checks, suggestions, first learning pass.
- Demo mode: a made-up person's week, in a separate Jarvis that sends nothing.
- Privacy: a copy of everything, and erase everything (to the Trash, or for good).
- Updates from the app, from the repository's release tags.
- The `jarvis` command and `install.sh`.
- Learning reads the apps every night, as Setup names them (ticket projects, wiki spaces, channels, words that mark work
  mail); Setup finds the Atlassian site.
- Linux and Windows: file locks, process groups and the exit-code wrapper go through one portable helper; `install.ps1`
  for Windows (not yet tried on a Windows computer).
- An API key in place of a Claude plan: the key in the system's password store, the cost of each run counted, background work held at a daily limit in dollars.
- The deploy gate, an optional part: reviews changes to your repositories (feature branches, deploy branches, release
  tags) before they go further and writes a GO / NO-GO verdict; it never pushes, merges or deploys.
- Optional parts: listening extras for the Mac app, pull-request watch, read-only access to your repositories.

## 0.0.1

The first working copy: the assistant, its server and screens, the knowledge base tools, the worker skills, the Mac
app, the tests.
