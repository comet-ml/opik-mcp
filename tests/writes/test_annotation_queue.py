"""``annotation_queue.create`` and ``.update`` through the dispatcher, against a mocked backend."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from opik_mcp.client.opik import OpikClient
from opik_mcp.config import Settings
from opik_mcp.writes.dispatch import run_write
from opik_mcp.writes.errors import ValidationFailedError

OPIK_BASE = "https://opik.test"
QUEUES = "/v1/private/annotation-queues"
PROJECT_ID = "0193a300-0000-7000-8000-00000000b001"
QUEUE_ID = "0193a300-0000-7000-8000-00000000a001"
VIEW = {
    "html": "<script>kit.mount(() => kit.h('p', { text: 'hi' }));</script>",
    "summary": "Annotators see a greeting.",
}
SETTINGS = Settings(opik_api_key="key-abc", comet_workspace="ws", opik_url=f"{OPIK_BASE}/api")
QUEUE_PAGE = f"{OPIK_BASE}/ws/projects/{PROJECT_ID}/annotation-queues"
ANNOTATOR_PAGE = f"{OPIK_BASE}/ws/sme?queueId={QUEUE_ID}"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


def _queue(*, with_view: bool) -> dict[str, object]:
    queue: dict[str, object] = {"id": QUEUE_ID, "project_id": PROJECT_ID, "name": "q"}
    if with_view:
        queue["annotator_view"] = {**VIEW, "kit_version": 1}
    return queue


async def _write(operation: str, data: dict[str, object]) -> dict[str, object]:
    return await run_write(operation=operation, data=data, client=_client(), settings=SETTINGS)


@pytest.mark.anyio
async def test_create_sends_the_view_with_the_kit_version_and_links_the_queue() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        post = mock.post(QUEUES).mock(return_value=httpx.Response(201))
        mock.get(f"{QUEUES}/{QUEUE_ID}").mock(
            return_value=httpx.Response(200, json=_queue(with_view=True))
        )
        out = await _write(
            "annotation_queue.create",
            {
                "id": QUEUE_ID,
                "project_id": PROJECT_ID,
                "name": "q",
                "scope": "trace",
                "annotator_view": VIEW,
            },
        )
    sent = json.loads(post.calls.last.request.content)
    assert sent["annotator_view"] == {**VIEW, "kit_version": 1}
    assert sent["project_id"] == PROJECT_ID
    assert out["url"] == f"{QUEUE_PAGE}/{QUEUE_ID}"
    assert out["annotator_url"] == ANNOTATOR_PAGE
    assert "id" not in out, "the caller already has the id it sent"
    assert "warning" not in out


@pytest.mark.anyio
async def test_a_create_without_an_id_answers_with_the_id_the_backend_chose() -> None:
    """No view, so no read-back: the id and link come from Location alone."""
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        mock.post(QUEUES).mock(
            return_value=httpx.Response(
                201, headers={"Location": f"{OPIK_BASE}{QUEUES}/{QUEUE_ID}"}
            )
        )
        read_back = mock.get(f"{QUEUES}/{QUEUE_ID}")
        out = await _write(
            "annotation_queue.create", {"project_id": PROJECT_ID, "name": "q", "scope": "trace"}
        )
    assert out["id"] == QUEUE_ID
    assert out["url"] == f"{QUEUE_PAGE}/{QUEUE_ID}"
    assert out["annotator_url"] == ANNOTATOR_PAGE
    assert not read_back.called


@pytest.mark.anyio
async def test_a_create_without_an_id_checks_the_view_of_the_queue_in_location() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(QUEUES).mock(
            return_value=httpx.Response(
                201, headers={"Location": f"{OPIK_BASE}{QUEUES}/{QUEUE_ID}"}
            )
        )
        read_back = mock.get(f"{QUEUES}/{QUEUE_ID}").mock(
            return_value=httpx.Response(200, json=_queue(with_view=True))
        )
        out = await _write(
            "annotation_queue.create",
            {"project_id": PROJECT_ID, "name": "q", "scope": "trace", "annotator_view": VIEW},
        )
    assert out["url"] == f"{QUEUE_PAGE}/{QUEUE_ID}"
    assert read_back.called


@pytest.mark.anyio
async def test_a_backend_that_drops_the_view_is_named_in_the_answer() -> None:
    """Released Opik before custom views ignores the field and answers 204."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"{QUEUES}/{QUEUE_ID}").mock(
            return_value=httpx.Response(200, json=_queue(with_view=False))
        )
        mock.patch(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(204))
        out = await _write("annotation_queue.update", {"id": QUEUE_ID, "annotator_view": VIEW})
    assert out["ok"] is True
    assert "did not store the annotator view" in str(out["warning"])


@pytest.mark.anyio
async def test_a_failed_read_back_still_reports_the_write_as_done(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="opik_mcp.writes.operations.annotation_queue")
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"{QUEUES}/{QUEUE_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_queue(with_view=False)),
                httpx.Response(500),
            ]
        )
        mock.patch(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(204))
        out = await _write("annotation_queue.update", {"id": QUEUE_ID, "annotator_view": VIEW})
    assert out["ok"] is True
    assert "warning" in out
    assert "did not store" not in str(out["warning"]), "an unread view is not a dropped one"
    assert "view read-back failed" in caplog.text


@pytest.mark.anyio
async def test_clearing_the_view_sends_an_empty_object_and_reads_nothing_back() -> None:
    """``annotator_view: None`` is dropped from the body, which the backend
    reads as "unchanged"; an empty object is what clears it."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        get = mock.get(f"{QUEUES}/{QUEUE_ID}").mock(
            return_value=httpx.Response(200, json=_queue(with_view=True))
        )
        patch = mock.patch(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(204))
        out = await _write(
            "annotation_queue.update", {"id": QUEUE_ID, "clear_annotator_view": True}
        )
    assert json.loads(patch.calls.last.request.content) == {"annotator_view": {}}
    assert get.call_count == 1, "only the project lookup before the write"
    assert out["url"] == f"{QUEUE_PAGE}/{QUEUE_ID}"
    assert out["annotator_url"] == ANNOTATOR_PAGE
    assert "warning" not in out


@pytest.mark.anyio
async def test_an_update_of_a_missing_queue_is_refused_before_the_write() -> None:
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        mock.get(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(404))
        patch = mock.patch(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(204))
        with pytest.raises(ValidationFailedError, match="queue_not_found"):
            await _write("annotation_queue.update", {"id": QUEUE_ID, "name": "renamed"})
    assert not patch.called


@pytest.mark.anyio
async def test_a_failed_project_lookup_costs_only_the_link(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="opik_mcp.writes.operations.annotation_queue")
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(500))
        patch = mock.patch(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(204))
        out = await _write("annotation_queue.update", {"id": QUEUE_ID, "name": "renamed"})
    assert patch.called
    assert out["ok"] is True
    assert "url" not in out
    assert "project lookup failed" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("view", "code"),
    [
        ({"html": VIEW["html"]}, "missing"),
        ({**VIEW, "summary": "Annotators see the email.\nThen the reply."}, "summary_multiline"),
    ],
)
async def test_a_view_needs_a_one_line_summary_since_the_queue_form_shows_it(
    view: dict[str, str], code: str
) -> None:
    """An update replaces the whole view, so html sent alone would erase the summary."""
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        patch = mock.patch(f"{QUEUES}/{QUEUE_ID}").mock(return_value=httpx.Response(204))
        with pytest.raises(ValidationFailedError, match=code):
            await _write("annotation_queue.update", {"id": QUEUE_ID, "annotator_view": view})
    assert not patch.called


@pytest.mark.anyio
async def test_setting_and_clearing_the_view_at_once_is_refused() -> None:
    with pytest.raises(ValidationFailedError, match="view_conflict"):
        await run_write(
            operation="annotation_queue.update",
            data={"id": QUEUE_ID, "annotator_view": VIEW, "clear_annotator_view": True},
            dry_run=True,
        )


@pytest.mark.anyio
async def test_a_view_over_the_backend_cap_is_refused_without_echoing_it() -> None:
    html = "x" * 200_001
    with pytest.raises(ValidationFailedError) as refused:
        await run_write(
            operation="annotation_queue.update",
            data={"id": QUEUE_ID, "annotator_view": {"html": html}},
            dry_run=True,
        )
    assert "string_too_long" in str(refused.value)
    assert html not in str(refused.value)
