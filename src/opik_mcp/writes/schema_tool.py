"""``schema`` MCP tool — lookup of an operation's JSON Schema (spec §2.2).

Pure registry lookup — no BE call, no auth state changes. The same
``expected_schema`` blob returned here is also embedded in
``validation_failed`` errors from the ``write`` tool, so callers that
prefer to optimistically attempt a write and recover on error never need
to call this tool.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.reference import LIST_SCHEMA_KEYS, list_reference
from opik_mcp.writes.errors import UnknownOperationError
from opik_mcp.writes.registry import WRITE_OPERATIONS, WRITE_REGISTRY

# Issue ``code`` values any operation can emit. Domain-specific codes are
# added per-op via ``WriteOperation.failure_modes``.
_UNIVERSAL_FAILURE_MODES: tuple[str, ...] = (
    "missing",
    "extra_forbidden",
    "type_mismatch",
    "float_parsing",
    "int_parsing",
    "string_too_long",
    "literal_error",
    "uuid_parsing",
    "datetime_parsing",
    "empty_batch",
    "batch_unsupported",
    "batch_too_large",
)


SCHEMA_KEYS: tuple[str, ...] = (*WRITE_OPERATIONS, *LIST_SCHEMA_KEYS)
"""Everything ``schema`` answers for: write operations plus ``list.<entity>``."""


def run_schema(operation: str) -> dict[str, Any]:
    """Return ``{schema, example, oauth_scope, supports_batch, parent_id_fields,
    failure_modes, description}`` for a write operation, or the filter/sort
    reference for a ``list.<entity>`` key."""
    if operation in LIST_SCHEMA_KEYS:
        return list_reference(operation.removeprefix("list."))
    op = WRITE_REGISTRY.get(operation)
    if op is None:
        # Mirror the structured envelope ``write`` raises so callers get
        # the same recovery surface (``valid_operations`` + fuzzy
        # ``did_you_mean``) on either tool — no asymmetric error shapes
        # for the model to learn. The ``from err`` chain lets analytics
        # unwrap to the typed cause and bucket as validation/400.
        err = UnknownOperationError.build(operation, SCHEMA_KEYS)
        raise ToolError(err.to_json()) from err
    return {
        "operation": op.name,
        "schema": op.pydantic_model.model_json_schema(),
        "example": op.example,
        "oauth_scope": op.oauth_scope,
        "supports_batch": op.supports_batch,
        "parent_id_fields": list(op.parent_id_fields),
        # Universal codes first, domain-specific codes appended. Callers can
        # preempt the known failure surface before sending; an LLM seeing
        # ``test_suite_parent_missing`` here will include a parent id from
        # the start instead of recovering from a 400.
        "failure_modes": list(_UNIVERSAL_FAILURE_MODES) + list(op.failure_modes),
        "description": op.description,
    }


__all__ = ["SCHEMA_KEYS", "run_schema"]
