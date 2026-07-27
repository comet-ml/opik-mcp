---
name: opik-sdk
description: Complete Opik Python SDK API reference for the imperative client — datasets, experiments, traces, spans, prompts, annotation queues, feedback scores, conversations, and evaluations.
last_updated: "2026-04-17"
source_commit: "2.0.0"
---

# Opik SDK Reference

The complete SDK API reference follows in the references below. See the `opik_sdk` tool description for script rules and environment setup.

## Restrictions

**Resolve entity names before scripting.** The Opik backend matches `project_name`, dataset names, and other entity-name kwargs **exactly** — a one-letter drift (e.g. "Optimization" vs "Optimizer") raises `ApiError` with status 404, not a fuzzy match. Before writing a script that passes an entity name the user mentioned conversationally, confirm the exact name via `list("<entity_type>")` (or a prior `read`) — unless the name was already returned verbatim by an earlier tool call in this conversation. This applies to `client.search_traces`, `client.search_spans`, `client.get_dataset`, `client.get_experiment_by_name`, and any other `*_name=` kwarg.

**Prompts live in the Opik Prompt library.** Use `client.get_prompt()` / `client.get_chat_prompt()` to fetch and `client.create_prompt()` / `client.create_chat_prompt()` to seed. Call them inside a `@opik.track`-decorated function so the prompt version is linked to the trace. The retired `opik.Prompt(...)` / `opik.ChatPrompt(...)` / `opik.Config` constructors should not be used. See the `instrumentation-prompts` skill for the full pattern.
