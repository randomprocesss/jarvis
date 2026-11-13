---
name: jarvis-profile-coder
description: Coder profile for a Jarvis task that changes code in one of the owner's repositories. Used together with jarvis-inbox-work when Jarvis's router picks "coder". Plan first, tests must pass, a code review before push; it never removes any tool or step.
---
# Coder profile

Code work goes on a branch as jarvis-inbox-work §3d says (a worktree, a branch named `{{branch_prefix}}<ticket or topic>-<slug>`,
push your own branch, never merge or push a shared branch). These rules are in addition, and Jarvis's acceptance check
verifies them before you may finish:

1. **Plan first for anything non-trivial** (more than one file, or any change in behaviour): write `PLAN.md` in the item's
   outputs folder covering the files, the change and how you will test it, then implement it. A one-line fix needs no plan.
2. **Tests pass before you commit.** Run the repository's tests (find the command in its README, CLAUDE.md, pyproject,
   package.json or Makefile) and the new or changed tests. Record the result on the work entry as
   `tests: {"cmd": "...", "passed": N, "failed": 0}`. Never commit with failing tests. If the repository has no tests,
   write a focused one for your change and record that.
3. **Review the diff before pushing** with the code-review skill when it is installed, else by reading your own diff
   line by line as a reviewer would. Fix what it finds, then record
   `code_review: {"findings": N, "fixed": N, "notes": "..."}` on the work entry.
4. [[if no_ai_words]]Commits, branch names and pull-request text never mention Claude, Jarvis or AI. [[end]]A merge is an approval
   request (jarvis-inbox-work §4b), never done by you.
5. **Checks are robust:** what you say a change fixes, or how it behaves once deployed, is shown by a run (a test, a
   reproduced case, a simulated merge), not inferred from the diff (jarvis-inbox-work §3f).

**Where the procedure lives** (jarvis-inbox-work keeps it whole; read those sections first): §3d (branch, worktree, push
your own branch, merge is an approval), §3e (failures), §4b (a merge request in approvals.json), §5 (record `branch`,
`tests`, `code_review` on the work entry).
