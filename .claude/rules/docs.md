---
paths:
  - "docs/**"
---

# Docs

- Tracked: `docs/README.md`, `docs/<feature>/design-doc.md` and
  `docs/decisions/`. Everything else under `docs/` is local. The repo is
  public: no customer or workspace names, hosts, keys.
- A design doc says what the feature does now and links the tests that prove
  it. A PR that changes behaviour updates the doc in the same PR.
- An ADR has four parts: decision, why, enforced by, log. "Enforced by" names
  tests that exist. Changing a decision is a dated log line or a superseding
  ADR, never an edit to the decision.
- No number or count a test owns. Link the test.
- A log entry is one dated line: what changed, why, the PR.
- Delete a stale doc. Don't annotate it.

Good:

```
2026-09-11: compression tiers removed; they cut answers the caller could not get back (#187).
```

Bad:

```
We came to understand that the budget, far from deciding the tier, had been believing the header all along.
```
