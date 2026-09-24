---
# A command, not a skill, and user-only: see .claude/rules/skills.md.
disable-model-invocation: true
description: Use this branch's MCP server like a customer, next to main, and report what a customer would notice changed.
argument-hint: "[extra flows or focus]"
---

Test this branch the way a customer would use it, through a headless session
that loads this branch's server next to main's, and tell me if it is ready
to ship. Extra focus from me:
$ARGUMENTS

If the branch doesn't touch `src/`, both servers run the same code and no
differences is the expected result. Say that in one line and still report
problems found on both.

## 1. Build the two servers

Nothing gets registered in any Claude config, so no restart is needed and no
other session pays for these servers.

```bash
python3 scripts/dev/install_branch.py dogfood-prepare [--workspace <ws>]
```

This builds this branch and a fresh `origin/main` into their own venvs and
writes a private MCP config naming them `opik-branch` and `opik-base`, on the
same workspace. The config holds `${OPIK_API_KEY}`, never the key.

## 2. Read what changed and what we already know

- `git diff origin/main...HEAD`, the commit messages, and the ticket
  (OPIK-<n> from the branch name, via Jira if available).
- Every file in `.claude/dogfood/memory/` (format in its README).

## 3. Pick the flows

Core flows, phrased the way a customer would ask:

1. How is my project doing?
2. What is broken in production?
3. Compare these two experiments.
4. Find the case where the judge disagreed.
5. Show me the traces with errors from the last hour.
6. Explain this trace.

Add 2 to 4 flows aimed straight at what the diff changed. The servers load
only in the headless run, so describe what to pick ("the largest recent
trace", "two experiments on one dataset") and let that session find real ids
with `list`. Don't invent ids.

## 4. Run the flows in a headless session

Write the flows from step 3 to a prompt file under the scratchpad or
`/tmp`, with these instructions for the headless session:

- Make the same calls with the same arguments through `opik-branch` and
  `opik-base`. Answer each flow as a customer would expect, then compare:
  is the answer right for the data, do the UI links look right, what does it
  cost (the size header, branch vs base), does anything say more than the
  data supports or cut without saying so?
- Put each difference in one bucket: **intended** (matches the diff or
  ticket), **regression** (worse on the branch), **pre-existing** (wrong on
  both), **improvement** (better, and not the point of the change).
- Skip these accepted findings: <paste each `accepted` entry's symptom,
  unless a file under its `recheck_when` changed in this branch; then ask for
  a re-check and a "same" or "changed" answer>.
- Read only. No write operations.
- Return the table from step 5 and candidate memory entries.

Then run it:

```bash
python3 scripts/dev/install_branch.py dogfood-run --prompt-file <file>
```

The key reaches the headless session through its environment only.

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

Afterwards, offer to remove the venvs and the base worktree:
`python3 scripts/dev/install_branch.py dogfood-clean`.
