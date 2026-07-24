"""Unit tests for the ``read`` tool — dispatch, name-lookup, errors.

Uses a duck-typed ``FakeOpikClient`` so we can exercise the registry's
fetcher functions without spinning up httpx mocks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.opik_client import OpikNotFoundError, OpikServerError
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.read_tool import run_read


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeOpikClient:
    """Stand-in for ``OpikClient`` — only implements the read endpoints."""

    projects_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    projects_by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    traces_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    spans_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace_spans: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    experiments_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    experiments_by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    test_suites_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompts_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompt_versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    threads_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    thread_messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    fail_list_traces: bool = False

    async def get_project(self, project_id: str) -> dict[str, Any]:
        if project_id not in self.projects_by_id:
            raise OpikNotFoundError(f"project {project_id!r} not found (404).")
        return self.projects_by_id[project_id]

    async def list_projects(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        content = self.projects_by_name.get(name or "", [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        if trace_id not in self.traces_by_id:
            raise OpikNotFoundError(f"trace {trace_id!r} not found (404).")
        return self.traces_by_id[trace_id]

    async def list_spans(
        self,
        *,
        trace_id: str,
        project_id: str | None = None,
        project_name: str | None = None,
        page: int = 1,
        size: int = 100,
    ) -> dict[str, Any]:
        content = self.trace_spans.get(trace_id, [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def get_span(self, span_id: str) -> dict[str, Any]:
        return self.spans_by_id[span_id]

    async def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        return self.experiments_by_id[experiment_id]

    async def list_experiments(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        content = self.experiments_by_name.get(name or "", [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def get_test_suite(self, test_suite_id: str) -> dict[str, Any]:
        return self.test_suites_by_id[test_suite_id]

    async def list_test_suites(self, **_: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def get_prompt(self, prompt_id: str) -> dict[str, Any]:
        return self.prompts_by_id[prompt_id]

    async def list_prompt_versions(
        self,
        prompt_id: str,
        *,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        content = self.prompt_versions.get(prompt_id, [])
        return {"content": content, "page": page, "size": len(content), "total": len(content)}

    async def list_prompts(self, **_: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def get_thread(
        self,
        thread_id: str,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        truncate: bool = False,
    ) -> dict[str, Any]:
        if thread_id not in self.threads_by_id:
            raise OpikNotFoundError(f"thread {thread_id!r} not found (404).")
        return self.threads_by_id[thread_id]

    async def list_traces(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        if self.fail_list_traces:
            raise OpikServerError("boom")
        # Honor a thread_id filter so the thread fetcher's messages call works.
        if filters:
            for f in json.loads(filters):
                if f.get("field") == "thread_id":
                    content = self.thread_messages.get(f.get("value"), [])
                    return {
                        "content": content,
                        "page": page,
                        "size": len(content),
                        "total": len(content),
                    }
        return {"content": [], "page": page, "size": 0, "total": 0}

    # OpikListClient surface the read tool doesn't exercise — present so the
    # fake satisfies the Protocol structurally.
    async def list_threads(self, **_: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}

    async def list_test_suite_items(self, _test_suite_id: str, **_kw: Any) -> dict[str, Any]:
        return {"content": [], "page": 1, "size": 0, "total": 0}


UUID = "11111111-2222-3333-4444-555555555555"


# --- happy paths ---------------------------------------------------------- #


@pytest.mark.anyio
async def test_read_project_by_uuid_returns_header_and_json() -> None:
    fake = FakeOpikClient(projects_by_id={UUID: {"id": UUID, "name": "demo"}})
    out = await run_read("project", UUID, client=fake)

    assert out.startswith(f"[read: project {UUID}")
    assert "compression=FULL" in out
    assert UUID in out
    assert "demo" in out


@pytest.mark.anyio
async def test_read_trace_inlines_spans() -> None:
    fake = FakeOpikClient(
        traces_by_id={UUID: {"id": UUID, "name": "t", "project_id": "p-1"}},
        trace_spans={UUID: [{"id": "sp-1", "name": "child"}]},
    )
    out = await run_read("trace", UUID, client=fake)
    assert '"spans"' in out
    assert "sp-1" in out
    assert '"spansTruncated"' in out
    assert "false" in out


@pytest.mark.anyio
async def test_read_trace_without_project_id_returns_empty_spans() -> None:
    fake = FakeOpikClient(traces_by_id={UUID: {"id": UUID, "name": "t"}})
    out = await run_read("trace", UUID, client=fake)
    assert '"spans": []' in out


@pytest.mark.anyio
async def test_read_prompt_inlines_versions() -> None:
    fake = FakeOpikClient(
        prompts_by_id={UUID: {"id": UUID, "name": "p"}},
        prompt_versions={UUID: [{"id": "v-1"}]},
    )
    out = await run_read("prompt", UUID, client=fake)
    assert '"versions"' in out
    assert "v-1" in out
    assert '"versionsTruncated"' in out


# --- name lookup ---------------------------------------------------------- #


@pytest.mark.anyio
async def test_read_project_by_name_resolves_to_unique_match() -> None:
    """Name → id resolution: fetch hits the resolved record. The header echoes
    the original input (so the user sees what they asked for); the body
    carries the resolved record."""
    fake = FakeOpikClient(
        projects_by_id={UUID: {"id": UUID, "name": "demo"}},
        projects_by_name={"demo": [{"id": UUID, "name": "demo"}]},
    )
    out = await run_read("project", "demo", client=fake)
    assert UUID in out
    assert "demo" in out


@pytest.mark.anyio
async def test_read_project_by_ambiguous_name_lists_candidates() -> None:
    fake = FakeOpikClient(
        projects_by_name={
            "demo": [
                {"id": "p-1", "name": "demo"},
                {"id": "p-2", "name": "demo-2"},
            ]
        },
    )
    with pytest.raises(ToolError) as exc:
        await run_read("project", "demo", client=fake)
    assert "Multiple projects match" in str(exc.value)
    assert "p-1" in str(exc.value)
    assert "p-2" in str(exc.value)
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_trace_skips_name_lookup_id_only_entity() -> None:
    """Traces are id-only — non-UUID input goes straight to fetch, which 404s."""
    fake = FakeOpikClient()
    with pytest.raises(ToolError, match="Not found"):
        await run_read("trace", "not-a-uuid", client=fake)


# --- URI input ------------------------------------------------------------ #


@pytest.mark.anyio
async def test_read_accepts_opik_uri_overriding_entity_type() -> None:
    """Passing a URI to ``id`` flips ``entity_type`` to match the URI."""
    fake = FakeOpikClient(projects_by_id={UUID: {"id": UUID, "name": "demo"}})
    out = await run_read("trace", f"opik://projects/{UUID}", client=fake)
    assert f"[read: project {UUID}" in out


@pytest.mark.anyio
async def test_read_rejects_malformed_uri() -> None:
    with pytest.raises(ToolError, match="opik://"):
        await run_read("trace", "opik://nonsense/x", client=FakeOpikClient())


# --- threads -------------------------------------------------------------- #

THREAD = "conv-1"
_THREAD_META = {"id": THREAD, "status": "active", "project_id": "p-9"}


def _thread_fake() -> FakeOpikClient:
    return FakeOpikClient(
        threads_by_id={THREAD: _THREAD_META},
        thread_messages={
            THREAD: [
                {"id": "tr-2", "name": "turn2", "input": "b", "start_time": "2026-01-02"},
                {"id": "tr-1", "name": "turn1", "input": "a", "start_time": "2026-01-01"},
            ]
        },
    )


@pytest.mark.anyio
async def test_read_thread_assembles_sorted_messages() -> None:
    out = await run_read("thread", THREAD, project_id="p-9", client=_thread_fake())
    assert f"[read: thread {THREAD}" in out
    assert '"messages"' in out
    assert '"messagesTruncated": false' in out
    # ascending by start_time → tr-1 (2026-01-01) precedes tr-2 (2026-01-02)
    assert out.index("tr-1") < out.index("tr-2")
    assert '"trace_id"' in out


@pytest.mark.anyio
async def test_read_thread_via_project_name() -> None:
    out = await run_read("thread", THREAD, project_name="demo", client=_thread_fake())
    assert f"[read: thread {THREAD}" in out


@pytest.mark.anyio
async def test_read_thread_requires_project() -> None:
    with pytest.raises(ToolError) as exc:
        await run_read("thread", THREAD, client=_thread_fake())
    assert "requires project scope" in str(exc.value)
    assert isinstance(exc.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_thread_via_canonical_uri_carries_project() -> None:
    # URI supplies the project, so no explicit project arg is needed.
    out = await run_read("thread", "opik://projects/p-9/threads/conv-1", client=_thread_fake())
    assert f"[read: thread {THREAD}" in out


@pytest.mark.anyio
async def test_read_thread_via_web_url() -> None:
    url = "https://app.opik.test/ws/projects/p-9/traces?thread=conv-1"
    out = await run_read("thread", url, client=_thread_fake())
    assert f"[read: thread {THREAD}" in out


@pytest.mark.anyio
async def test_read_thread_degrades_on_messages_failure() -> None:
    fake = _thread_fake()
    fake.fail_list_traces = True
    out = await run_read("thread", THREAD, project_id="p-9", client=fake)
    assert '"messages": []' in out
    assert '"messagesTruncated": false' in out
    # the load failure is surfaced, not silently reported as "no messages"
    assert "messagesError" in out
    # metadata still present
    assert '"status"' in out


@pytest.mark.anyio
async def test_read_thread_not_found() -> None:
    fake = FakeOpikClient()
    with pytest.raises(ToolError, match="Not found"):
        await run_read("thread", THREAD, project_id="p-9", client=fake)


# --- validation / errors -------------------------------------------------- #


@pytest.mark.anyio
async def test_read_rejects_unknown_entity_type() -> None:
    with pytest.raises(ToolError, match="Invalid entity_type"):
        await run_read("widget", UUID, client=FakeOpikClient())


@pytest.mark.anyio
async def test_read_rejects_list_only_entity() -> None:
    with pytest.raises(ToolError, match="list-only"):
        await run_read("test_suite_item", UUID, client=FakeOpikClient())


@pytest.mark.anyio
async def test_read_surfaces_not_found_with_hint() -> None:
    fake = FakeOpikClient()
    with pytest.raises(ToolError) as exc:
        await run_read("project", UUID, client=fake)
    msg = str(exc.value)
    assert "Not found" in msg
    assert UUID in msg


# --- compression budget --------------------------------------------------- #


@pytest.mark.anyio
async def test_read_respects_max_tokens() -> None:
    fake = FakeOpikClient(projects_by_id={UUID: {"id": UUID, "blob": "x" * 50_000}})
    out = await run_read("project", UUID, max_tokens=100, client=fake)
    assert "compression=MEDIUM" in out


# --- exception chain assertions ------------------------------------------- #


@pytest.mark.anyio
async def test_read_list_only_entity_chains_typed_cause() -> None:
    """``read('test_suite_item', '<uuid>')`` — list-only entity surfaced as
    ToolError chained from EntityArgValidationError so the analytics wrapper
    buckets it as validation/400 instead of unknown."""
    with pytest.raises(ToolError) as ei:
        await run_read("test_suite_item", "00000000-0000-0000-0000-000000000000")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_read_invalid_entity_type_chains_typed_cause() -> None:
    """``read('not_real', '<uuid>')`` — invalid entity_type chained from
    EntityArgValidationError so the bucket is validation/400, not unknown."""
    with pytest.raises(ToolError) as ei:
        await run_read("not_real", "00000000-0000-0000-0000-000000000000")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)
