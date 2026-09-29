"""The thread lifecycle writes over both transports: closing and reopening.

Their body is built in ``src/opik_mcp/writes/operations/threads.py``, which
also resolves a thread comment's model id; that case is in
``test_observability``, with the rest of ``comment.create``.
"""

from __future__ import annotations

import pytest

from tests.hermetic.stub_records import PROJECT_ID, THREAD_ID
from tests.hermetic.writes.surface import (
    LOGS,
    Sent,
    Wire,
    WriteCase,
    assert_refused_before_anything_is_sent,
    assert_the_write_lands_and_links,
    operations_of,
)

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]


CASES: tuple[WriteCase, ...] = (
    WriteCase(
        case_id="thread.close",
        operation="thread.close",
        data={"thread_id": THREAD_ID, "project_id": PROJECT_ID},
        sent=(
            Sent(
                "PUT",
                "/v1/private/traces/threads/close",
                {"thread_id": THREAD_ID, "project_id": PROJECT_ID},
            ),
        ),
        url_suffix=f"{LOGS}?logsType=threads&thread={THREAD_ID}",
    ),
    WriteCase(
        case_id="thread.open",
        operation="thread.open",
        data={"thread_id": THREAD_ID, "project_id": PROJECT_ID},
        sent=(
            Sent(
                "PUT",
                "/v1/private/traces/threads/open",
                {"thread_id": THREAD_ID, "project_id": PROJECT_ID},
            ),
        ),
        url_suffix=f"{LOGS}?logsType=threads&thread={THREAD_ID}",
    ),
)


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_a_write_sends_its_request_and_answers_with_where_to_look(
    wire: Wire, case: WriteCase
) -> None:
    await assert_the_write_lands_and_links(wire, case)


@pytest.mark.parametrize("operation", operations_of(CASES))
async def test_a_payload_the_operation_does_not_accept_is_refused_before_anything_is_sent(
    wire: Wire, operation: str
) -> None:
    """The ``validation_failed`` envelope carries what the caller needs to fix
    the call in one turn: which field, a working example, the retry call, and
    the ``schema()`` call that returns the schema, which it no longer inlines."""
    await assert_refused_before_anything_is_sent(wire, operation)
