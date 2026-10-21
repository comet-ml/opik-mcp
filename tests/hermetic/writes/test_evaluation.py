"""The evaluation writes over both transports: prompts, datasets and experiments.

Their hooks are ``src/opik_mcp/writes/operations/evaluation.py``.
"""

from __future__ import annotations

import pytest

from tests.hermetic.stub_records import EXPERIMENT_A, PROMPT_NAME, SUITE_NAME, TRACE_ID
from tests.hermetic.writes.surface import (
    DATASET_ITEM_ID,
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
        case_id="prompt_version.save",
        operation="prompt_version.save",
        data={
            "name": PROMPT_NAME,
            "template": "Answer the refund question in one line.",
            "commit": "v4",
            "change_description": "shorter",
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/prompts/versions",
                {
                    "name": PROMPT_NAME,
                    "version": {
                        "template": "Answer the refund question in one line.",
                        "commit": "v4",
                    },
                    "change_description": "shorter",
                },
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="dataset.create",
        operation="dataset.create",
        data={"name": "refund-cases", "type": "test_suite"},
        # The backend still spells a test suite ``evaluation_suite``.
        sent=(
            Sent(
                "POST",
                "/v1/private/datasets",
                {"name": "refund-cases", "type": "evaluation_suite"},
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="dataset_item.upsert",
        operation="dataset_item.upsert",
        data={
            "dataset_name": SUITE_NAME,
            "items": [
                {"input": {"question": "refund?"}, "expected_output": {"answer": "5-7 days"}},
                {"input": {"question": "exchange?"}},
            ],
        },
        sent=(
            Sent(
                "PUT",
                "/v1/private/datasets/items",
                {
                    "dataset_name": SUITE_NAME,
                    "items": [
                        {
                            "data": {
                                "input": {"question": "refund?"},
                                "expected_output": {"answer": "5-7 days"},
                            },
                            "source": "sdk",
                        },
                        {"data": {"input": {"question": "exchange?"}}, "source": "sdk"},
                    ],
                },
            ),
        ),
        url_suffix=None,
        # Counted through the envelope, not as the one object that carries it.
        item_count=2,
    ),
    WriteCase(
        case_id="experiment.create",
        operation="experiment.create",
        data={"dataset_name": SUITE_NAME, "name": "rerank-v4"},
        sent=(
            Sent(
                "POST",
                "/v1/private/experiments",
                {"dataset_name": SUITE_NAME, "name": "rerank-v4"},
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="experiment_item.create",
        operation="experiment_item.create",
        data={
            "experiment_items": [
                {
                    "experiment_id": EXPERIMENT_A,
                    "dataset_item_id": DATASET_ITEM_ID,
                    "trace_id": TRACE_ID,
                }
            ]
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/experiments/items",
                {
                    "experiment_items": [
                        {
                            "experiment_id": EXPERIMENT_A,
                            "dataset_item_id": DATASET_ITEM_ID,
                            "trace_id": TRACE_ID,
                        }
                    ]
                },
            ),
        ),
        url_suffix=None,
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
