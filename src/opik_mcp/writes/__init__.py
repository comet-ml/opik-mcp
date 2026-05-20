"""Universal ``write`` + ``schema`` tool surface (spec 2026-05-18).

Supersedes the narrow ``score`` / ``comment`` tools (ADR 0004 §D2). The
agent picks an entity/verb pair via the ``operation`` enum and hands a
``data`` payload; the dispatcher validates against a Pydantic model,
checks the operation's OAuth scope, and dispatches the right BE call.

The public API is intentionally tiny — ``run_write`` and ``run_schema``
plus the structured ``WriteError`` hierarchy. Everything else (registry,
models, dispatch) lives in submodules so the MCP tool layer only depends
on these two entrypoints.
"""

from opik_mcp.writes.description import SCHEMA_TOOL_DESCRIPTION, WRITE_TOOL_DESCRIPTION
from opik_mcp.writes.errors import (
    AuthorizationDeniedError,
    BackendError,
    BatchPartialFailureError,
    BatchTooLargeError,
    UnknownOperationError,
    ValidationFailedError,
    ValidationIssue,
    WriteError,
)
from opik_mcp.writes.registry import WRITE_OPERATIONS, WRITE_REGISTRY, WriteOperation
from opik_mcp.writes.schema_tool import run_schema
from opik_mcp.writes.scopes import ALL_WRITE_SCOPES
from opik_mcp.writes.write_tool import run_write

__all__ = [
    "ALL_WRITE_SCOPES",
    "SCHEMA_TOOL_DESCRIPTION",
    "WRITE_OPERATIONS",
    "WRITE_REGISTRY",
    "WRITE_TOOL_DESCRIPTION",
    "AuthorizationDeniedError",
    "BackendError",
    "BatchPartialFailureError",
    "BatchTooLargeError",
    "UnknownOperationError",
    "ValidationFailedError",
    "ValidationIssue",
    "WriteError",
    "WriteOperation",
    "run_schema",
    "run_write",
]
