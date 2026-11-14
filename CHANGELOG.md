# Changes

Each release is a git tag `vX.Y.Z` with the same number as the `VERSION` file. The app offers a release only when its
tag is newer than the copy you run, and only updates on your press.

## Unreleased

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
- Optional parts: listening extras for the Mac app, pull-request watch, read-only access to your repositories.

## 0.0.1

The first working copy: the assistant, its server and screens, the knowledge base tools, the worker skills, the Mac
app, the tests.
