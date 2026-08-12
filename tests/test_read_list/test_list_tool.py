"""Unit tests for the ``list`` tool — table shape, required kwargs, pagination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.list_tool import run_list


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeOpikClient:
    """Just enough surface for the list tool to drive the registry."""

    projects: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    experiments: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    prompts: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    test_suites: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    traces: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    test_suite_items: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    prompt_versions: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    threads: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    annotation_queues: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    feedback_definitions: dict[str, Any] = field(
        default_factory=lambda: {"content": [], "total": 0}
    )

    last_kwargs: dict[str, Any] = field(default_factory=dict)

    async def list_projects(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.projects

    async def list_experiments(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.experiments

    async def list_prompts(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.prompts

    async def list_test_suites(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.test_suites

    async def list_traces(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.traces

    async def list_threads(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.threads

    async def list_annotation_queues(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.annotation_queues

    async def list_feedback_definitions(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.feedback_definitions

    async def list_test_suite_items(self, test_suite_id: str, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = {"test_suite_id": test_suite_id, **kw}
        return self.test_suite_items

    async def list_prompt_versions(self, prompt_id: str, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = {"prompt_id": prompt_id, **kw}
        return self.prompt_versions

    async def list_spans(self, **_: Any) -> dict[str, Any]:
        # Not exercised by the list tool (span has no list_fn) — included to
        # satisfy the OpikListClient Protocol structurally.
        return {"content": [], "page": 1, "size": 0, "total": 0}


# --- table format --------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_projects_renders_pipe_delimited_table() -> None:
    fake = FakeOpikClient(
        projects={
            "content": [
                {"id": "p-1", "name": "demo", "created_at": "2026-01-01"},
                {"id": "p-2", "name": "other", "created_at": "2026-02-01"},
            ],
            "total": 2,
        }
    )
    out = await run_list("project", client=fake)
    assert "Found 2 projects" in out
    assert "id | name | created_at" in out
    assert "p-1 | demo | 2026-01-01" in out
    assert "p-2 | other | 2026-02-01" in out


@pytest.mark.anyio
async def test_list_renders_empty_state_message() -> None:
    out = await run_list("project", client=FakeOpikClient())
    assert "No projects found" in out


@pytest.mark.anyio
async def test_list_with_name_filter_in_header_and_empty_message() -> None:
    fake = FakeOpikClient(experiments={"content": [], "total": 0})
    out = await run_list("experiment", name="zzz", client=fake)
    assert "No experiments matching 'zzz' found" in out
    assert fake.last_kwargs.get("name") == "zzz"


@pytest.mark.anyio
async def test_list_appends_pagination_hint_when_more_results() -> None:
    fake = FakeOpikClient(
        projects={"content": [{"id": "p-1", "name": "a"}], "total": 50},
    )
    out = await run_list("project", page=1, size=10, client=fake)
    assert "Use page=2 for next 10 results" in out


@pytest.mark.anyio
async def test_list_truncates_long_values_at_sixty_chars() -> None:
    fake = FakeOpikClient(
        projects={"content": [{"id": "p-1", "name": "x" * 100}], "total": 1},
    )
    out = await run_list("project", client=fake)
    assert "..." in out
    assert "x" * 60 not in out  # truncated form is 57 chars + "..."


# --- required kwargs ----------------------------------------------------- #


@pytest.mark.anyio
async def test_list_traces_requires_project_id() -> None:
    with pytest.raises(ToolError, match="requires project_id"):
        await run_list("trace", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_traces_with_project_id_forwards_kwarg() -> None:
    fake = FakeOpikClient(traces={"content": [], "total": 0})
    await run_list("trace", project_id="p-1", client=fake)
    assert fake.last_kwargs.get("project_id") == "p-1"


@pytest.mark.anyio
async def test_list_test_suite_items_requires_test_suite_id() -> None:
    with pytest.raises(ToolError, match="requires test_suite_id"):
        await run_list("test_suite_item", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_prompt_versions_requires_prompt_id() -> None:
    with pytest.raises(ToolError, match="requires prompt_id"):
        await run_list("prompt_version", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_threads_requires_project_id() -> None:
    with pytest.raises(ToolError, match="requires project_id"):
        await run_list("thread", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_threads_accepts_project_name_alternative() -> None:
    """project_name satisfies the project requirement (no UUID round-trip)."""
    fake = FakeOpikClient(threads={"content": [{"id": "th-1", "status": "active"}], "total": 1})
    out = await run_list("thread", project_name="support-bot", client=fake)
    assert fake.last_kwargs.get("project_name") == "support-bot"
    assert "project_id" not in fake.last_kwargs
    assert "th-1" in out


@pytest.mark.anyio
async def test_list_traces_accepts_project_name_alternative() -> None:
    fake = FakeOpikClient(traces={"content": [], "total": 0})
    await run_list("trace", project_name="support-bot", client=fake)
    assert fake.last_kwargs.get("project_name") == "support-bot"


@pytest.mark.anyio
async def test_list_thread_missing_project_error_mentions_name() -> None:
    with pytest.raises(ToolError, match=r"project_id \(or project_name\)"):
        await run_list("thread", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_project_name_not_forwarded_to_workspace_wide_list() -> None:
    """project_name must not leak into a non-project-scoped list_fn as a kwarg."""
    fake = FakeOpikClient(experiments={"content": [], "total": 0})
    await run_list("experiment", project_name="ignored", client=fake)
    assert "project_name" not in fake.last_kwargs


@pytest.mark.anyio
async def test_list_threads_renders_thread_columns() -> None:
    fake = FakeOpikClient(
        threads={
            "content": [
                {
                    "id": "conv-1",
                    "status": "active",
                    "number_of_messages": 4,
                    "last_updated_at": "2026-01-02",
                }
            ],
            "total": 1,
        }
    )
    out = await run_list("thread", project_id="p-1", client=fake)
    assert fake.last_kwargs.get("project_id") == "p-1"
    # id column carries the thread identifier; extras render status + counts.
    assert "conv-1" in out
    assert "status" in out
    assert "number_of_messages" in out
    assert "4" in out


# --- entity-type validation ---------------------------------------------- #


@pytest.mark.anyio
async def test_list_rejects_unknown_entity_type() -> None:
    with pytest.raises(ToolError, match="Cannot list 'widget'"):
        await run_list("widget", client=FakeOpikClient())


@pytest.mark.anyio
async def test_list_rejects_span_singleton_entity() -> None:
    """``span`` has no list_fn — it's id-only."""
    with pytest.raises(ToolError, match="Cannot list 'span'"):
        await run_list("span", client=FakeOpikClient())


# --- size / page clamping ------------------------------------------------ #


@pytest.mark.anyio
async def test_list_clamps_size_to_max() -> None:
    fake = FakeOpikClient()
    await run_list("project", size=500, client=fake)
    assert fake.last_kwargs.get("size") == 100


@pytest.mark.anyio
async def test_list_unknown_entity_type_chains_typed_cause() -> None:
    """``list('wat')`` raises ToolError, but the cause must be the typed
    EntityArgValidationError so the analytics wrapper buckets it as
    validation/400 rather than unknown."""
    with pytest.raises(ToolError) as ei:
        await run_list("not_a_real_type")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)


@pytest.mark.anyio
async def test_list_missing_required_kwarg_chains_typed_cause() -> None:
    """``list('trace')`` without ``project_id`` raises ToolError; the cause
    must be the typed EntityArgValidationError. Same chaining contract."""
    with pytest.raises(ToolError) as ei:
        await run_list("trace")

    assert isinstance(ei.value.__cause__, EntityArgValidationError)
