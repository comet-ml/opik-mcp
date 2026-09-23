# 0003 — Five tools

Status: accepted, 2026-09-23 (OPIK-8485)

## Decision

The server advertises exactly five tools: `read`, `list`, `write`, `schema`
and `read_skill`.

- Reads are tools over an entity registry, not MCP resources.
- All writes go through one `write(operation, data)` tool over an operation
  registry. `schema(operation)` returns an operation's input on demand, so
  the surface doesn't carry every schema.
- Each answer is returned once, as text. No `structuredContent` copy.
- Adding or removing a tool is a deliberate change with a reviewer.

## Why

Fewer, general tools keep the surface small (0001) and make tool choice easy
for the model. A new entity or operation is a registry entry, not a new tool.

## Enforced by

- `tests/conformance/test_tool_inventory.py`: the exact set and the budget.
- `tests/conformance/test_schema_snapshots.py`: input schemas change only on
  purpose (`UPDATE_SNAPSHOTS=1`).
- `tests/conformance/test_no_duplicate_payload.py`: one copy per answer.
- `tests/conformance/test_write_tool_surface.py`: `write` and `schema` match
  the registry.

## Log

- 2026-05-18: one `write` tool over a registry replaced the narrow write tools.
- 2026-09-01: `read_skill` added (#175).
- 2026-09-03: `ask_ollie` and `run_experiment` removed (#181).
- 2026-09-23: duplicate `structuredContent` removed (#201).
