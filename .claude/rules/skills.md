---
paths:
  - "src/opik_mcp/skills/**"
---

# Authored skills

- Skills pass the agentskills.io validator, are named `opik-*`, and the name
  matches the folder (`tests/test_skills_spec_compliance.py`).
- Check for the Opik MCP first; SDK scripting is the fallback.
- Keep provenance under `metadata:`. Update it when content is re-checked
  against Opik.
- Routing between skills lives in `skills_catalog`; don't repeat it in
  instructions or in another skill.
- Skills are authored only here. Nothing under `.claude/skills/` or
  `.agents/skills/`.

## Why dev procedures are commands, not skills

Other repos keep their dev procedures as skills in `.claude/skills/` or
`.agents/skills/` and sync them across tools. Here that would ship them to
users: `npx skills add comet-ml/opik-mcp` resolves anything in those folders,
and `make skills-verify-source` fails. So dev procedures live in
`.claude/commands/`, marked `disable-model-invocation: true` when they have
side effects. Don't move them.
