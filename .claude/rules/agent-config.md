---
paths:
  - "AGENTS.md"
  - "CLAUDE.md"
  - ".claude/**"
---

# Agent configuration

- `AGENTS.md` is a map, not a manual: where things are, what runs, what
  breaks. A line stays only if removing it would cause a mistake. Procedures
  go in a command, facts about one area in a path-scoped rule.
- A fact lives once. The test owns the number, the ADR owns the reason, the
  rule owns the instruction. Link, never copy.
- A rule a linter or test can check goes in `pyproject.toml` or `tests/`, and
  the rule text says only "`make check`".
- Every judgement rule carries a Good and a Bad example from this repo.
- Rules have `paths:` frontmatter. Always-on rules (no `paths`) stay a few
  lines each, because they load in every session.
- A command that pushes, posts or changes user configuration carries
  `disable-model-invocation: true`.
- A subagent gets the smallest `tools` list that does the job, the cheapest
  model that does it well, and a one-sentence description of when to use it.
- A hook matches narrowly, exits 0 on input it doesn't understand, and exits 2
  with the reason and where the file belongs. Every hook has a test in
  `tests/repo/test_agent_hooks.py`.
- Nothing under `.claude/skills/` or `.agents/skills/`. `npx skills add`
  ships them to users. Developer procedures are commands.
- Memory under `.claude/dogfood/memory/`: one file per entry, no workspace or
  customer names, `accepted` only after a developer says so.

Good: `- The tool set is pinned … tests/conformance/test_tool_inventory.py`
Bad: `- Exactly five tools.` (a number the test already owns, copied here)
