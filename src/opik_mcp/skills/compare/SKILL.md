---
name: compare
description: Run a candidate against the baseline over a test suite and show the numbers (server-side experiment). Does not issue a ship/no-ship verdict.
last_updated: "2026-07-30"
source_commit: "TODO"
---

# Compare — candidate vs baseline over a suite   <!-- SCAFFOLD (OPIK-7651) -->

> **Scaffold — not yet implemented.** Behavior is specified below; the steps are TODO.

## Intent
Run a server-side experiment over a stored suite (from `/test`), then surface the compare-view link and per-metric deltas. The mechanical half of verification — **not** the verdict (deciding ship/no-ship is a later phase).

## Transport
Runs via the SDK REST client (`rest_client.experiments.execute_experiment` -> `POST /v1/private/experiments/execute`) or the hosted MCP `run_experiment` tool (recommended) — the same backend endpoint. Fire-and-return (the run takes 10–30 min server-side); poll status.

## Definition of done
- [ ] Returns experiment(s) + compare link + per-metric deltas; works over the SDK, uses the MCP when present.
- [ ] No ship/no-ship verdict.
