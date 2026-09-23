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
