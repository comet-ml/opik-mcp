"""``annotation_queue`` through read and list: the html stated by size, the project scope."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.protocols import OpikListClient, OpikReadClient
from opik_mcp.config import Settings
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from tests.factories import make_settings

QUEUE_ID = "0193a300-0000-7000-8000-00000000a001"
PROJECT_ID = "0193a300-0000-7000-8000-00000000b001"
HTML = "<script>kit.mount(({ trace }) => kit.card(trace.output));</script>" * 50


def _settings() -> Settings:
    return make_settings(
        opik_api_key="k", comet_workspace="demo-ws", opik_url="https://opik.test/api/"
    )


def _queue(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": QUEUE_ID,
        "project_id": PROJECT_ID,
        "name": "Email reply review",
        "scope": "trace",
        "items_count": 4,
        "annotator_view": {"html": HTML, "kit_version": 1, "summary": "Annotators see the reply."},
    }
    record.update(overrides)
    return record


@dataclass
class FakeQueues:
    record: dict[str, object] = field(default_factory=_queue)
    page: dict[str, object] = field(default_factory=dict)
    list_calls: list[dict[str, object]] = field(default_factory=list)

    async def get_annotation_queue(self, queue_id: str) -> dict[str, object]:
        assert queue_id == self.record["id"]
        return self.record

    async def list_annotation_queues(self, **kwargs: object) -> dict[str, object]:
        self.list_calls.append(kwargs)
        return self.page or {"content": [], "total": 0}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_a_plain_read_states_the_html_by_size_and_names_the_call() -> None:
    out = await run_read(
        "annotation_queue",
        QUEUE_ID,
        settings=_settings(),
        client=cast("OpikReadClient", FakeQueues()),
    )

    assert HTML not in out
    body = json.loads(out.split("\n", 1)[1])
    assert body["annotator_view"] == {
        "kit_version": 1,
        "summary": "Annotators see the reply.",
        "html_chars": len(HTML),
    }
    assert f"{len(HTML):,} characters" in body["annotatorViewHtml"]
    assert "fields=['annotator_view.html']" in body["annotatorViewHtml"]


@pytest.mark.anyio
async def test_fields_returns_the_html_whole() -> None:
    out = await run_read(
        "annotation_queue",
        QUEUE_ID,
        settings=_settings(),
        client=cast("OpikReadClient", FakeQueues()),
        fields=["annotator_view.html"],
    )

    assert HTML in out


@pytest.mark.anyio
async def test_a_queue_without_a_view_says_annotators_see_the_default() -> None:
    fake = FakeQueues(record=_queue(annotator_view=None))
    out = await run_read(
        "annotation_queue", QUEUE_ID, settings=_settings(), client=cast("OpikReadClient", fake)
    )

    body = json.loads(out.split("\n", 1)[1])
    assert "annotator_view" not in body
    assert body["annotatorViewHtml"].startswith("none")


@pytest.mark.anyio
async def test_asking_for_the_html_of_a_queue_without_a_view_answers_null() -> None:
    fake = FakeQueues(record=_queue(annotator_view={}))
    out = await run_read(
        "annotation_queue",
        QUEUE_ID,
        settings=_settings(),
        client=cast("OpikReadClient", fake),
        fields=["annotator_view.html"],
    )

    body = json.loads(out.split("\n", 2)[2])
    assert body["annotator_view"] == {"html": None}


@pytest.mark.anyio
async def test_a_queue_is_read_by_its_name() -> None:
    fake = FakeQueues(
        page={"content": [{"id": QUEUE_ID, "name": "Email reply review", "project_name": "a"}]}
    )
    out = await run_read(
        "annotation_queue",
        "Email reply review",
        settings=_settings(),
        client=cast("OpikReadClient", fake),
    )

    assert fake.list_calls[-1]["name"] == "Email reply review"
    assert json.loads(out.split("\n", 1)[1])["id"] == QUEUE_ID


@pytest.mark.anyio
async def test_a_name_two_projects_share_lists_each_queue_with_its_project() -> None:
    other = "0193a300-0000-7000-8000-00000000a002"
    fake = FakeQueues(
        page={
            "content": [
                {"id": QUEUE_ID, "name": "Reply review", "project_name": "support"},
                {"id": other, "name": "Reply review", "project_name": "sales"},
            ]
        }
    )
    with pytest.raises(ToolError) as refused:
        await run_read(
            "annotation_queue",
            "Reply review",
            settings=_settings(),
            client=cast("OpikReadClient", fake),
        )

    assert "Reply review (project support)" in str(refused.value)
    assert "Reply review (project sales)" in str(refused.value)


@pytest.mark.anyio
async def test_a_read_links_the_queue_page_under_its_project() -> None:
    out = await run_read(
        "annotation_queue",
        QUEUE_ID,
        settings=_settings(),
        client=cast("OpikReadClient", FakeQueues()),
    )

    body = json.loads(out.split("\n", 1)[1])
    assert body["url"] == (
        f"https://opik.test/demo-ws/projects/{PROJECT_ID}/annotation-queues/{QUEUE_ID}"
    )


@pytest.mark.anyio
async def test_a_list_sends_the_project_as_a_filter_clause_beside_the_callers() -> None:
    fake = FakeQueues(page={"content": [_queue(annotator_view=None)], "total": 1})

    out = await run_list(
        "annotation_queue",
        project_id=PROJECT_ID,
        filters='scope = "thread"',
        settings=_settings(),
        client=cast("OpikListClient", fake),
    )

    sent = json.loads(str(fake.list_calls[-1]["filters"]))
    assert {"field": "project_id", "operator": "=", "key": "", "value": PROJECT_ID} in sent
    assert any(clause["field"] == "scope" for clause in sent)
    assert "Email reply review" in out


@pytest.mark.anyio
async def test_a_list_without_a_project_is_refused() -> None:
    with pytest.raises(ToolError, match="project_id"):
        await run_list(
            "annotation_queue", settings=_settings(), client=cast("OpikListClient", FakeQueues())
        )
