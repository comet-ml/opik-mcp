"""The annotation queue writes over both transports, and the view an older Opik drops.

Their hooks are ``src/opik_mcp/writes/operations/annotation_queue.py``. A write
that carries a view reads the queue back; the stub's
``annotation_queue_keeps_view`` plays an Opik from before custom views.
"""

from __future__ import annotations

import pytest

from tests.hermetic.servers import WORKSPACE
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import ANNOTATION_QUEUE_ID, PROJECT_ID, PROJECT_NAME
from tests.hermetic.writes.surface import (
    Sent,
    Wire,
    WriteCase,
    assert_refused_before_anything_is_sent,
    assert_the_write_lands_and_links,
    operations_of,
)

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

QUEUES = f"/{WORKSPACE}/projects/{PROJECT_ID}/annotation-queues"
VIEW = {"html": "<script>kit.mount(() => kit.h('p', { text: 'hi' }));</script>"}


def _drops_views(stub: StubBackend) -> None:
    stub.annotation_queue_keeps_view = False


CASES: tuple[WriteCase, ...] = (
    WriteCase(
        case_id="annotation_queue.create",
        operation="annotation_queue.create",
        data={
            "id": ANNOTATION_QUEUE_ID,
            "project_name": PROJECT_NAME,
            "name": "reply-review",
            "scope": "trace",
            "annotator_view": VIEW,
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/annotation-queues",
                {
                    "id": ANNOTATION_QUEUE_ID,
                    "project_id": PROJECT_ID,
                    "name": "reply-review",
                    "scope": "trace",
                    "annotator_view": {**VIEW, "kit_version": 1},
                },
            ),
        ),
        url_suffix=f"{QUEUES}/{ANNOTATION_QUEUE_ID}",
        extra_keys=frozenset({"annotator_url"}),
    ),
    WriteCase(
        case_id="annotation_queue.update-clears-the-view",
        operation="annotation_queue.update",
        data={"id": ANNOTATION_QUEUE_ID, "clear_annotator_view": True},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/annotation-queues/{ANNOTATION_QUEUE_ID}",
                {"annotator_view": {}},
            ),
        ),
        url_suffix=f"{QUEUES}/{ANNOTATION_QUEUE_ID}",
        extra_keys=frozenset({"annotator_url"}),
    ),
    WriteCase(
        case_id="annotation_queue.update-on-an-opik-without-views",
        operation="annotation_queue.update",
        data={"id": ANNOTATION_QUEUE_ID, "annotator_view": VIEW},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/annotation-queues/{ANNOTATION_QUEUE_ID}",
                {"annotator_view": {**VIEW, "kit_version": 1}},
            ),
        ),
        url_suffix=f"{QUEUES}/{ANNOTATION_QUEUE_ID}",
        arrange=_drops_views,
        extra_keys=frozenset({"annotator_url", "warning"}),
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
    await assert_refused_before_anything_is_sent(wire, operation)
