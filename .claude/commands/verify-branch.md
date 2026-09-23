---
description: Test this branch like a customer in the current session, compare it with main, and report bugs and regressions.
argument-hint: "[extra flows or focus]"
---

Test this branch the way a customer would use it, through the MCP tools in
this session, and tell me if it is ready to ship. Extra focus from me:
$ARGUMENTS

## 1. Set up two servers on the same workspace

- Branch server: `opik-<ticket>` from this worktree. If it is missing or older
  than the last commit, run `/install-branch`.
- Baseline server: `opik-base`, built from a fresh `origin/main` worktree
  (`git worktree add` under `.claude/worktrees/base`, then
  `make install-branch NAME=base WORKSPACE=<branch server's workspace>`).
  Never reuse or change `opik-main` or other servers; they may point at
  another workspace.
- Both must use the same workspace, or every data difference looks like a
  regression. Check with `claude mcp get`, without showing the env block.
- New servers load only after a restart. If you installed one, stop and tell
  me to restart, then rerun this command.

## 2. Read what changed and what we already know

- `git diff origin/main...HEAD`, the commit messages, and the ticket
  (OPIK-<n> from the branch name, via Jira if available).
- Every file in `.claude/verify-branch/memory/` (format in its README).

## 3. Pick the flows

Core flows, phrased the way a customer would ask:

1. How is my project doing?
2. What is broken in production?
3. Compare these two experiments.
4. Find the case where the judge disagreed.
5. Show me the traces with errors from the last hour.
6. Close this thread. (Only on a throwaway thread you create; confirm with me first.)
7. Explain this trace.

Add 2 to 4 flows aimed straight at what the diff changed. Pick real projects,
experiments and traces from the workspace; don't invent ids.

## 4. Run each flow on both servers

Make the same calls with the same arguments through `opik-<ticket>` and
`opik-base`. Answer the flow as a customer would expect, then compare:

- Is the answer right for the data? Open the UI links and check they work.
- What does it cost? Note the size header, and compare it with the baseline.
- Does anything say more than the data supports, or cut without saying so?

Put each difference in one bucket:

- **intended**: matches the diff or the ticket.
- **regression**: worse on the branch.
- **pre-existing**: wrong on both.
- **improvement**: better on the branch, and not the point of the change.

Skip anything that matches an `accepted` memory entry, unless a file under its
`recheck_when` changed in this branch. Then re-test it and report "previously
accepted, re-checked: same" or "changed".

## 5. Report

A table: flow, call, branch answer, base answer (short), bucket, UI link.
Then the verdict, one of:

- **ship**: no regressions, and the change does what the ticket says.
- **fix first**: list the regressions and bugs, most serious first.
- **needs your eyes**: something you couldn't judge; say what.

Keep the report short. Don't list flows that matched and have nothing to say.

## 6. Propose memory entries

List the entries you suggest, numbered, with kind and one line each. Write
`backlog` and `context` entries yourself and say so. Write an `accepted` entry
only after I reply with its number. No workspace or customer names in entries.

Afterwards, offer to remove `opik-base` (`make uninstall-branch NAME=base`) and
its worktree.
