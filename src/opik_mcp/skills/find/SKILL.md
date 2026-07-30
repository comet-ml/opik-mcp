---
name: find
description: Surface the traces worth attention in a workspace — errors, regressions, outliers, low online-eval scores — plus Diagnostics issues. Scoped to online/production signal.
last_updated: "2026-07-30"
source_commit: "TODO"
---

# Find — surface the traces worth attention   <!-- SCAFFOLD (OPIK-7648) -->

> **Scaffold — not yet implemented.** Behavior is specified below; the steps are TODO.

## Intent
Rank live/production traces by signal (errors, latency, regressions, low online-eval scores) and surface Diagnostics issues, so the developer sees what needs attention without hunting. Hands a chosen trace to `/explain`.

## Scope
Online / production signal only. Offline experiment results are the output of `/compare` and `/evaluate`, not re-discovered here.

## Transport
Reads Diagnostics via the SDK REST client (`rest_client.agent_insights`) or the hosted MCP `issue` entity when connected (recommended). Does not require the MCP.

## Definition of done
- [ ] Returns a ranked shortlist of traces/issues worth attention, including low online-eval scores.
- [ ] Works over the SDK; uses the MCP when present; does not surface offline results.
