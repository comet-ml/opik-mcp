---
description: Collect unaddressed review comments on this branch's PR, fix or answer each, and reply in the thread.
---

Handle the open review comments on this branch's PR in `comet-ml/opik-mcp`.

## 1. Find the PR

- `gh auth status`; stop if not logged in.
- `gh pr list --head <branch> --state open`. If none, say so and stop.

## 2. Collect comments

Always `--paginate`; without it, comments past the first 30 are missed.

```bash
gh api repos/comet-ml/opik-mcp/pulls/<n>/comments --paginate
gh api repos/comet-ml/opik-mcp/issues/<n>/comments --paginate
gh api repos/comet-ml/opik-mcp/pulls/<n>/reviews --paginate
```

A comment is pending when its thread is unresolved (GraphQL, step 5) and it
has no reply from us yet. Include bot reviewers.

## 3. Propose

One line each: `id`, `file:line`, a short quote, and what you propose: a code
change, an answer (the code is already right), or skip with a reason. Then
ask me: fix, answer, or skip, per comment.

## 4. Act and reply

Reply in the thread with `in_reply_to`:

```bash
gh api repos/comet-ml/opik-mcp/pulls/<n>/comments -f body="…" -F in_reply_to=<id>
```

- Answer or skip: reply now, one or two lines.
- Fix: make the change, run the relevant tests, commit, and push. Only after
  the push succeeds, reply `Fixed in <sha>: <what changed>`. Never post a
  "Fixed" reply for a commit that isn't on origin.

## 5. Resolve

Ask before resolving. Page through `reviewThreads` with GraphQL
(`first: 100`, follow `endCursor` until `hasNextPage` is false), and resolve
only the threads we replied to:

```bash
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<id>"}) { thread { isResolved } } }'
```

Report how many threads were resolved.
