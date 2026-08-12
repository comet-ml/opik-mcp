"""Unit tests for the MCP App data channel.

The panel and the model must resolve a reference the same way. When ``build_app_payload``
had its own fetch path, a pasted Opik link or an entity name worked in text and failed in
the panel — these tests pin the parity rather than the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from opik_mcp.apps import build_app_payload
from opik_mcp.config import Settings
from opik_mcp.read_list import run_list

QUEUE_ID = "0193a300-0000-7000-8000-0000000000q1".replace("q", "9")
PROJECT_ID = "0193a300-0000-7000-8000-000000000p11".replace("p", "9")
THREAD_ID = "conversation-42"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        opik_api_key="k",
        comet_workspace="acme",
        comet_url_override="https://dev.comet.com",
    )


@dataclass
class FakeClient:
    """Only the endpoints the two app entities touch."""

    definitions: list[dict[str, Any]] = field(default_factory=list)
    definition_pages: list[list[dict[str, Any]]] | None = None
    queues_by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    queue_threads: list[dict[str, Any]] = field(default_factory=list)
    seen_definition_pages: list[int] = field(default_factory=list)

    async def get_annotation_queue(self, queue_id: str) -> dict[str, Any]:
        return {
            "id": queue_id,
            "name": "refund triage",
            "project_id": PROJECT_ID,
            "scope": "thread",
            "feedback_definition_names": ["Policy accuracy"],
        }

    async def list_annotation_queues(
        self,
        *,
        name: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        content = self.queues_by_name.get(name or "", [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def list_feedback_definitions(
        self, *, name: str | None = None, page: int = 1, size: int = 100
    ) -> dict[str, Any]:
        self.seen_definition_pages.append(page)
        if self.definition_pages is not None:
            content = self.definition_pages[page - 1] if page <= len(self.definition_pages) else []
            total = sum(len(p) for p in self.definition_pages)
            return {"content": content, "page": page, "size": size, "total": total}
        return {"content": self.definitions, "page": page, "size": size, "total": 1}

    async def list_queue_threads(
        self, *, project_id: str, queue_id: str, page: int = 1, size: int = 100
    ) -> dict[str, Any]:
        return {
            "content": self.queue_threads,
            "page": page,
            "size": len(self.queue_threads),
            "total": len(self.queue_threads),
        }

    async def get_thread(
        self,
        thread_id: str,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        truncate: bool = False,
    ) -> dict[str, Any]:
        return {"id": thread_id, "project_id": project_id, "status": "inactive"}

    async def list_traces(self, **kwargs: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}


@pytest.mark.anyio
async def test_pasted_thread_link_resolves_for_the_panel(settings: Settings) -> None:
    """A link is the demo's entry point; it must carry its project into the panel too."""
    link = (
        f"https://dev.comet.com/opik/acme/projects/{PROJECT_ID}"
        f"/traces?tab=logs&logsType=threads&thread={THREAD_ID}"
    )
    payload = await build_app_payload(
        FakeClient(),  # type: ignore[arg-type]
        settings,
        entity_type="thread",
        entity_id=link,
    )
    assert payload["thread_id"] == THREAD_ID
    assert payload["project"]["id"] == PROJECT_ID
    assert THREAD_ID in (payload["url"] or "")


@pytest.mark.anyio
async def test_queue_name_resolves_for_the_panel(settings: Settings) -> None:
    """``review`` advertises "queue UUID or name"; the panel has to honour both."""
    client = FakeClient(
        queues_by_name={"refund triage": [{"id": QUEUE_ID, "name": "refund triage"}]},
        definitions=[{"name": "Policy accuracy", "type": "categorical", "details": {}}],
        queue_threads=[{"id": THREAD_ID, "number_of_messages": 4, "feedback_scores": []}],
    )
    payload = await build_app_payload(
        client,  # type: ignore[arg-type]
        settings,
        entity_type="annotation_queue",
        entity_id="refund triage",
    )
    assert payload["id"] == QUEUE_ID
    assert [d["name"] for d in payload["definitions"]] == ["Policy accuracy"]
    assert payload["items"][0]["thread_id"] == THREAD_ID
    assert QUEUE_ID in (payload["url"] or "")


@pytest.mark.anyio
async def test_queue_rubric_is_found_past_the_first_page(settings: Settings) -> None:
    """A workspace with many definitions must not silently lose the queue's rubric —
    that leaves the reviewer a panel with no scoring controls."""
    filler = [{"name": f"other-{i}", "type": "numerical", "details": {}} for i in range(100)]
    client = FakeClient(
        definition_pages=[
            filler,
            [{"name": "Policy accuracy", "type": "categorical", "details": {}}],
        ],
        queue_threads=[],
    )
    payload = await build_app_payload(
        client,  # type: ignore[arg-type]
        settings,
        entity_type="annotation_queue",
        entity_id=QUEUE_ID,
    )
    assert [d["name"] for d in payload["definitions"]] == ["Policy accuracy"]
    assert client.seen_definition_pages == [1, 2]


@pytest.mark.anyio
async def test_queue_list_accepts_project_scope() -> None:
    """Queues are project-scoped, so ``list('annotation_queue', project_id=…)`` is the
    natural call — it used to reach the client with an unexpected kwarg."""
    client = FakeClient(queues_by_name={"": []})
    out = await run_list(
        entity_type="annotation_queue",
        project_id=PROJECT_ID,
        client=client,  # type: ignore[arg-type]
    )
    assert "annotation_queue" in out
