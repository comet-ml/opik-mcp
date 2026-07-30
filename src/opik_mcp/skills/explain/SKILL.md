---
name: explain
description: Root-cause a specific trace or behavior — what went wrong and why. Reasons against your code by default (SDK); escalates to Ollie for workspace-wide investigation when the hosted MCP is connected.
last_updated: "2026-07-30"
source_commit: "TODO"
---

# Explain — root-cause a specific trace   <!-- SCAFFOLD (OPIK-7649) -->

> **Scaffold — not yet implemented.** Behavior is specified below; the steps are TODO.

## Intent
Given a trace (often from `/find`), produce a grounded root-cause: what went wrong, why, and a suggested next step, citing the relevant spans.

## Transport — two paths, one output
- **SDK (default):** fetch the trace + spans; the coding agent reasons **against your code**. No MCP required.
- **MCP / Ollie (when connected on Opik Cloud):** escalate to `ask_ollie` for workspace-wide investigation. `ask_ollie` is the only MCP-only capability.

Same output shape either way (root cause + evidence spans + next step).

## Definition of done
- [ ] Grounded root-cause over the SDK path with no MCP required.
- [ ] Escalates to Ollie when available; identical output shape.

## Open question
Validate whether Ollie out-root-causes the coding agent for a single trace (Ollie lacks repo context) before calling the Ollie path "recommended."
