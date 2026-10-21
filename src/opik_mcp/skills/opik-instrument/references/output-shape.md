---
last_updated: "2026-09-10"
source_commit: "2.0.0"
---

# Output shape

- `status`: `verified` | `blocked` | `already_verified` | `unsupported`
- `changes`: `files_changed`, `dependency_added`, `config_source`, `entrypoints_instrumented`, `integrations_added`
- `verification`: `command_run`, `trace_id`, `trace_url`
- `blocker`: `reason`, `next_step`
- `expansion_opportunities`: `prompts`, `threads`, `spans`

Invariants: `verified` must carry a `trace_id`/`trace_url`; `blocked` must carry exactly one `next_step` **and** still report `changes`; `already_verified` = existing instrumentation exercised and confirmed; `unsupported` explains the unsupported language/shape and **modifies nothing**.
