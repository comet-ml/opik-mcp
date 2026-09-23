# Commits and PRs

- Subject: `[OPIK-XXXX] <what changed, in plain words>`, about 60 characters.
  - Good: `[OPIK-8480] Link each span to its trace page`
  - Bad: `[OPIK-8480] Say the hazard once`
- Body optional, at most five lines: why, plus any number that matters.
- One PR, one ticket, one behaviour change. Shared groundwork is its own PR.
  Past about 800 changed lines, split.
- PR body follows `.github/pull_request_template.md`, states the context cost
  if any, fills AI-WATERMARK honestly.
- The ticket being resolved keeps its hyphen (`OPIK-8485`); related tickets
  are written `OPIK_1234` so Jira doesn't link them. Code and docs use the
  normal form.
- Open PRs as drafts. Never force-push a shared branch. Never push to `main`.
- Reply to every review thread: `Fixed in <sha>` after the push, or one line
  on why not.
- Plain style everywhere: commits, PR text, comments, docs. No metaphors.
