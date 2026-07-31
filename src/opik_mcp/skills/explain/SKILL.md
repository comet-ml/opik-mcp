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

## Transport
The agent runs the investigation itself over data it fetches via the SDK — a single trace, or a broader set (via `list` / `agent_insights`) for a pattern. **No MCP required.** When the hosted MCP is connected, it can optionally hand off to `ask_ollie` as an *alternative* reasoner — a convenience, not a gate. Output shape is the same either way (root cause + evidence spans + next step).

## Definition of done
- [ ] Grounded root-cause over the SDK path with no MCP required.
- [ ] Escalates to Ollie when available; identical output shape.

## Open question
Validate whether Ollie out-root-causes the coding agent for a single trace (Ollie lacks repo context) before calling the Ollie path "recommended."
