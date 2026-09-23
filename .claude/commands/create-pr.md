---
description: Run what CI runs, review the branch, and open a draft PR in comet-ml/opik-mcp with the template filled in.
---

Open a draft PR for the current branch in `comet-ml/opik-mcp`. Re-run every
step each time; never skip one because an earlier run passed.

## 1. Preflight

- `gh auth status`. If not logged in, stop and tell me to run `gh auth login`.
- Not on `main`. Take the ticket key from the branch name (`OPIK-1234`).
- If `gh pr list --head <branch> --state open` finds a PR, ask whether to
  update its body or stop.

## 2. Commit and push

- Tracked changes: stage and commit them in the style of
  `.claude/rules/git-workflow.md`.
- Untracked files: list them and ask which to add. Never `git add -A`.
- `git fetch origin main`. If the branch is behind, say by how many commits
  and ask. Don't rebase or force-push yourself.
- `git push -u origin HEAD`.

## 3. Checks

- Use the `test-runner` subagent to run all three CI targets: `make check`,
  `make e2e`, `make skills-verify-source`. If one fails, show the failures and
  ask whether to stop or open the PR anyway, marked in the Testing section.
- Use the `code-reviewer` subagent on the branch. Show me Critical and High
  findings and ask before continuing.

## 4. Body

Fill `.github/pull_request_template.md`. Short bullets, plain words.

- Title: `[OPIK-XXXX] <what changed>`, about 60 characters.
- Details: what changed for the user or the agent, then anything the reviewer
  should look at first. Include the context cost if the change adds any
  (surface bytes before → after, from the budget test).
- Issues: `Resolves OPIK-XXXX`. Related tickets as `OPIK_1234`.
- AI-WATERMARK: fill it honestly.
  ```
  AI-WATERMARK: yes
  - Tools: Claude Code
  - Model(s): <the model you are running as>
  - Scope: <full implementation | assisted | review only>
  - Human verification: <what the developer checked; ask me if unsure>
  ```
- Testing: the three targets and their counts, plus `/dogfood` if it
  was run.
- No customer or workspace names, internal hosts or secrets. The repo is
  public.

End the body with the attribution line from the session instructions, if
there is one.

## 5. Create

`gh pr create --draft --repo comet-ml/opik-mcp --title … --body-file …`.
Print the URL. It stays a draft until I mark it ready.
