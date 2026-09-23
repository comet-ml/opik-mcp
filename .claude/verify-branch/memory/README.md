# Verify memory

What `/verify-branch` should remember between runs: findings a developer
accepted, ideas for later, and facts that save time. One small file per entry,
so parallel branches never conflict. This folder is committed and the repo is
public: no customer or workspace names, keys or internal links.

## Entry format

File name: a short slug, e.g. `archived-experiments-not-listed.md`.

```markdown
---
kind: accepted        # accepted | backlog | context
flow: compare-experiments
symptom: "list('experiment') leaves out archived runs"
decided: 2026-09-23, Yaroslav, OPIK-8396
recheck_when: src/opik_mcp/read_list/entities/experiment.py
---
Archived runs are hidden in the UI too, so the list matches what users see.
```

- `accepted`: a known behaviour the developer approved. Not reported again.
- `backlog`: worth improving later; doesn't block a PR.
- `context`: a fact that saves time next run.

## Rules

- Only a developer approves an `accepted` entry. The agent proposes, the
  developer confirms, then the agent writes it.
- The agent may write `backlog` and `context` entries itself and must list them
  in its report.
- If a file under `recheck_when` changed in the branch, re-test the entry and
  report it as "previously accepted, re-checked: same" or "changed".
- Delete an entry when it no longer holds. Keep each body to one or two
  sentences.
