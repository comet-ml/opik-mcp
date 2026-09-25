"""What the dispatcher does for every operation: the credential it forwards,
the failures it reports, and the case table it is checked against."""

from __future__ import annotations

import pytest

from opik_mcp.writes.registry import WRITE_OPERATIONS
from tests.hermetic.servers import API_KEY, WORKSPACE, result_json
from tests.hermetic.writes import (
    test_diagnostics,
    test_evaluation,
    test_observability,
    test_threads,
)
from tests.hermetic.writes.surface import Wire, operations_of

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

_TABLES = (test_observability, test_threads, test_evaluation, test_diagnostics)


def test_every_write_operation_has_an_end_to_end_case() -> None:
    """A new operation needs a row in the ``CASES`` of its module's file."""
    covered = {operation for table in _TABLES for operation in operations_of(table.CASES)}
    missing = sorted(set(WRITE_OPERATIONS) - covered)
    assert not missing, (
        f"write operations with no hermetic case: {missing}. Add a WriteCase to CASES in "
        "tests/hermetic/writes/test_<module>.py, the module of "
        "src/opik_mcp/writes/operations/ whose hooks the operation uses."
    )


async def test_each_request_carries_the_callers_credential_and_workspace(wire: Wire) -> None:
    """Over HTTP the bearer is the caller's; the process holds no key at all."""
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": "dataset.create", "data": {"name": "x"}}
        )
    assert not result.isError, result_json(result)

    (request,) = wire.backend.writes()
    assert request.headers.get("comet-workspace") == WORKSPACE
    assert API_KEY in request.headers.get("authorization", "")


async def test_a_backend_failure_comes_back_as_its_status_and_the_retry_call(wire: Wire) -> None:
    """The status is kept for the caller and analytics; the body is untrusted
    text and the REST path is not a name the caller can use, so neither is."""
    wire.backend.failing.add("/v1/private/datasets")
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": "dataset.create", "data": {"name": "refund-cases"}}
        )
    assert result.isError
    envelope = result_json(result)

    assert envelope["error"] == "backend_error"
    assert envelope["operation"] == "dataset.create"
    assert envelope["backend_error"] == {"status": 500}
    assert "retry the same write('dataset.create', data=…)" in str(envelope["message"])
    assert "backend_message" not in envelope
    text = str(envelope)
    assert "stub failure" not in text
    assert "/v1/private" not in text


async def test_a_backend_rejection_quotes_only_its_error_strings(wire: Wire) -> None:
    wire.backend.rejecting.add("/v1/private/datasets")
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": "dataset.create", "data": {"name": "refund-cases"}}
        )
    assert result.isError
    envelope = result_json(result)

    assert envelope["backend_error"] == {"status": 400}
    assert envelope["backend_message"] == "name must be unique"
    assert "write('dataset.create', data=…)" in str(envelope["message"])
    text = str(envelope)
    assert "stub internals" not in text
    assert "/v1/private" not in text
