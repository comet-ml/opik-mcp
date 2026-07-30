---
name: instrument
description: Add Opik tracing to a codebase and verify a real trace lands. Detects language/framework, adds decorators/integrations, runs the app, and confirms a trace arrived. Runs `opik configure` first if the project is not configured.
last_updated: "2026-07-30"
source_commit: "TODO"
---

# Instrument — add tracing and verify a real trace   <!-- SCAFFOLD (OPIK-7473) -->

> **Scaffold — not yet implemented.** Behavior is specified below; the steps are TODO.

## Intent
Take a repo from no observability to a **verified** first trace: detect the language and LLM framework, add the minimal Opik tracing, run the app (or a representative path), confirm ingestion, and return the trace URL.

## Setup
If `~/.opik.config` is missing, run the existing `opik configure` (Python) / `npx opik-ts configure` (TypeScript) first. There is no separate config skill.

## Transport
Local: edits the repo and runs the app (SDK/CLI); confirms the trace via the SDK. Smoother with the hosted Opik MCP connected (recommended).

## Definition of done
- [ ] From an uninstrumented app, one invocation lands a **verified** trace and returns its URL (not a static audit).
- [ ] Runs `opik configure` when unconfigured; Python + TypeScript.
