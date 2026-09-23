# Commits and PRs

- Subject: `[OPIK-XXXX] <what changed, in plain words>`, about 60 characters.
  - Good: `[OPIK-8480] Move the link hazard note to the module docstring`
  - Bad: `[OPIK-8480] Say the hazard once`
- Body is optional, at most about five lines: why, plus any number that
  matters (surface bytes, test counts).
- PR body follows `.github/pull_request_template.md` with short bullets, and
  fills AI-WATERMARK honestly.
- The ticket a PR resolves keeps its hyphen (`OPIK-8485`). Write related
  tickets as `OPIK_1234` so Jira doesn't link them.
- Reply to each review thread: "Fixed in `<sha>`" after the push, or one line
  on why not.
- Open PRs as drafts. Never force-push a shared branch.
- The same plain style everywhere: commits, PR text, comments, docs. No
  metaphors, no capitalised "THE X" headings.
