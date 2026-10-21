"""The observability writes over both transports: traces, spans, scores and comments.

Their hooks are ``src/opik_mcp/writes/operations/observability.py``. A score or
a comment on a thread is here too, since the operation is observability's;
the thread comment also runs the threads hook that resolves its model id.
"""

from __future__ import annotations

import pytest

from tests.hermetic.stub_records import (
    PROJECT_ID,
    PROJECT_NAME,
    THREAD_ID,
    THREAD_MODEL_ID,
    TRACE_ID,
)
from tests.hermetic.writes.surface import (
    ENDED_AT,
    LOGS,
    NEW_SPAN_ID,
    NEW_TRACE_ID,
    STARTED_AT,
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
        case_id="trace.create",
        operation="trace.create",
        data={
            "id": NEW_TRACE_ID,
            "project_id": PROJECT_ID,
            "name": "checkout",
            "start_time": STARTED_AT,
            "input": {"question": "where is my refund?"},
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/traces",
                {
                    "id": NEW_TRACE_ID,
                    "project_id": PROJECT_ID,
                    "name": "checkout",
                    "start_time": STARTED_AT,
                    "input": {"question": "where is my refund?"},
                },
            ),
        ),
        url_suffix=f"{LOGS}?logsType=traces&trace={NEW_TRACE_ID}",
    ),
    WriteCase(
        case_id="trace.create-batch",
        operation="trace.create",
        data=[
            {"project_id": PROJECT_ID, "name": "first", "start_time": STARTED_AT},
            {"project_id": PROJECT_ID, "name": "second", "start_time": STARTED_AT},
        ],
        sent=(
            Sent(
                "POST",
                "/v1/private/traces/batch",
                {
                    "traces": [
                        {"project_id": PROJECT_ID, "name": "first", "start_time": STARTED_AT},
                        {"project_id": PROJECT_ID, "name": "second", "start_time": STARTED_AT},
                    ]
                },
            ),
        ),
        # A batch links to the page it landed on, never to one of its rows.
        url_suffix=f"{LOGS}?logsType=traces",
        item_count=2,
    ),
    WriteCase(
        case_id="trace.update",
        operation="trace.update",
        data={
            "id": TRACE_ID,
            "project_id": PROJECT_ID,
            "end_time": ENDED_AT,
            "output": {"answer": "5-7 business days"},
        },
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/traces/{TRACE_ID}",
                {
                    "project_id": PROJECT_ID,
                    "end_time": ENDED_AT,
                    "output": {"answer": "5-7 business days"},
                },
            ),
        ),
        url_suffix=f"{LOGS}?logsType=traces&trace={TRACE_ID}",
    ),
    WriteCase(
        case_id="span.create",
        operation="span.create",
        data={
            "id": NEW_SPAN_ID,
            "trace_id": TRACE_ID,
            "project_id": PROJECT_ID,
            "name": "openai.chat",
            "type": "llm",
            "start_time": STARTED_AT,
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/spans",
                {
                    "id": NEW_SPAN_ID,
                    "trace_id": TRACE_ID,
                    "project_id": PROJECT_ID,
                    "name": "openai.chat",
                    "type": "llm",
                    "start_time": STARTED_AT,
                },
            ),
        ),
        url_suffix=f"{LOGS}?logsType=traces&trace={TRACE_ID}&span={NEW_SPAN_ID}",
    ),
    WriteCase(
        case_id="score.create",
        operation="score.create",
        data={
            "target": "trace",
            "target_id": TRACE_ID,
            "project_id": PROJECT_ID,
            "name": "helpfulness",
            "value": 0.8,
        },
        sent=(
            Sent(
                "PUT",
                f"/v1/private/traces/{TRACE_ID}/feedback-scores",
                {"name": "helpfulness", "value": 0.8, "source": "sdk"},
            ),
        ),
        url_suffix=f"{LOGS}?logsType=traces&trace={TRACE_ID}",
    ),
    WriteCase(
        case_id="score.create-thread",
        operation="score.create",
        # A thread score has no singleton route; the array form is the only one.
        data=[
            {
                "target": "thread",
                "target_id": THREAD_ID,
                "project_name": PROJECT_NAME,
                "name": "resolved",
                "value": 1,
            }
        ],
        sent=(
            Sent(
                "PUT",
                "/v1/private/traces/threads/feedback-scores",
                {
                    "scores": [
                        {
                            "thread_id": THREAD_ID,
                            "project_name": PROJECT_NAME,
                            "name": "resolved",
                            "value": 1.0,
                            "source": "sdk",
                        }
                    ]
                },
            ),
        ),
        # A project name is not resolved after a write has succeeded, so there
        # is no project id to build a link from.
        url_suffix=None,
    ),
    WriteCase(
        case_id="comment.create",
        operation="comment.create",
        data={
            "target": "trace",
            "target_id": TRACE_ID,
            "project_id": PROJECT_ID,
            "text": "retry with temperature=0",
        },
        sent=(
            Sent(
                "POST",
                f"/v1/private/traces/{TRACE_ID}/comments",
                {"text": "retry with temperature=0"},
            ),
        ),
        url_suffix=f"{LOGS}?logsType=traces&trace={TRACE_ID}",
    ),
    WriteCase(
        case_id="comment.create-thread",
        operation="comment.create",
        data={
            "target": "thread",
            "target_id": THREAD_ID,
            "project_id": PROJECT_ID,
            "text": "customer asked twice",
        },
        # The caller names the thread by its thread_id; the comment route takes
        # the model UUID, which the server looks up first.
        sent=(
            Sent(
                "POST",
                f"/v1/private/traces/threads/{THREAD_MODEL_ID}/comments",
                {"text": "customer asked twice"},
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
