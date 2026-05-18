"""Registry-generated tool description prose (spec §6).

The description is the highest-leverage place to teach the model the
operation enum, so it's regenerated from the registry at module load —
never hand-maintained. A conformance test asserts that every registry
entry appears verbatim, so adding a new operation without updating the
description is caught at CI time.
"""

from __future__ import annotations

from typing import Final

from opik_mcp.writes.registry import WRITE_REGISTRY, WriteOperation


def _operation_line(op: WriteOperation) -> str:
    parent = f"; required: {', '.join(op.parent_id_fields)}" if op.parent_id_fields else ""
    batch = " (batch ok)" if op.supports_batch else ""
    return f"- {op.name}: {op.description}{parent}{batch}"


def build_write_description() -> str:
    lines = [
        "Create, update, or annotate Opik entities. The `operation` field "
        "selects which entity/verb pair to invoke; `data` carries the payload "
        "for that operation (single object, or array up to 1000 for batch).",
        "",
        "Operations:",
    ]
    lines.extend(_operation_line(op) for op in WRITE_REGISTRY.values())
    lines.extend(
        [
            "",
            "Notes:",
            "- `data` shape: object for single, array for batch (where supported). "
            "Always-envelope ops (test_suite_item.upsert, experiment_item.create) "
            "take their list inside the envelope, not at the top level.",
            "- `dry_run=true`: validate + check authorization without calling the "
            "backend. Returns {dry_run, would_call: {method, path, body_size}}.",
            "- On a schema mismatch the tool returns a `validation_failed` error "
            "carrying the exact JSON Schema for `data` plus one corrected example. "
            "Call `schema(operation)` ahead of time only when you want the schema "
            "without attempting a write.",
        ]
    )
    return "\n".join(lines)


WRITE_TOOL_DESCRIPTION: Final[str] = build_write_description()
SCHEMA_TOOL_DESCRIPTION: Final[str] = (
    "Return the JSON Schema, OAuth scope, and one validated example for a "
    "write operation's `data` payload. Pure lookup — no backend call."
)


__all__ = [
    "SCHEMA_TOOL_DESCRIPTION",
    "WRITE_TOOL_DESCRIPTION",
    "build_write_description",
]
