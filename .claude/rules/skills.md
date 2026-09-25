---
paths:
  - "src/opik_mcp/skills/**"
---

# Authored skills

- Skills are authored here and nowhere else. `.claude/skills/` and
  `.agents/skills/` are read by `npx skills add` and would ship to users. A
  hook blocks the file tools there; don't route around it with the shell.
- The name is `opik` or `opik-<verb>` and matches the folder. The spec validator in
  `tests/skills/test_spec_compliance.py` is the contract.
- The description is the trigger. It says when to use this skill and when to
  use its neighbour. A description change re-runs the skill's trigger evals in
  its `evals/` folder, where it has one, before the PR.
- Check for the Opik MCP first; SDK scripting is the fallback, and the skill
  says which one it used.
- Routing between skills lives in `skills_catalog`. A skill never describes
  another skill.
- Provenance under `metadata:`. Update it when the content is re-checked
  against Opik, with the date.
- Reference material goes in `references/`, loaded on demand. `SKILL.md` stays
  short enough to read in one go.

Good, from `opik-compare`: "Run a candidate against the baseline over an Opik
test suite and read the numbers back: which cases broke, which got fixed…".
Bad: "Helps you work with Opik experiments."
