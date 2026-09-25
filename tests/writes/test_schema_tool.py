"""Tests for ``opik_mcp.writes.schema_tool``.

The schema tool is a synchronous registry lookup — no BE call, no auth.
The only failure path that lives here is the unknown-operation case at
``schema_tool.py:46``: it must wrap the typed ``UnknownOperationError``
in a ``ToolError`` while preserving the chain via ``from err`` so the
analytics wrapper buckets the failure as validation/400 (not unknown).
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.writes.errors import UnknownOperationError
from opik_mcp.writes.schema_tool import run_schema


def test_schema_unknown_operation_chains_typed_cause() -> None:
    """``schema('not_a_real_op')`` raises ToolError chained from
    UnknownOperationError so the analytics wrapper buckets it as
    validation/400 via the typed exception's ClassVars."""
    with pytest.raises(ToolError) as ei:
        run_schema("not_a_real_op")

    assert isinstance(ei.value.__cause__, UnknownOperationError)
