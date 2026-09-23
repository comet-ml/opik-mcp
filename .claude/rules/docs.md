---
paths:
  - "docs/**"
---

# Docs

- Tracked: `docs/README.md`, `docs/<feature>/design-doc.md`,
  `docs/decisions/`. Everything else under `docs/` is local.
- The repo is public: no customer or workspace names, internal links, keys.
- Write short, dated log entries of one or two lines. Say what was decided and
  why. No essays, no metaphors.
- An ADR names the test that enforces it. When a decision changes, add a line
  to its log or write a new ADR that supersedes it.
- Delete a stale doc rather than annotating it.

Good: `2026-09-11: compression tiers removed; they cut answers the caller could not get back (#187).`

Bad: `We came to understand that the budget, far from deciding the tier, had been believing the header all along.`
