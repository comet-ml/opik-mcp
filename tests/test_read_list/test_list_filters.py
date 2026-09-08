"""``list`` tool — filters, sort, time window and search (OPIK-8283).

Everything here is observed through ``run_list`` with a fake client: the
arguments that reach the backend, the table text, and the error text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.oql import OQLError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _page(items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"content": items, "page": 1, "size": len(items), "total": len(items), **extra}


@dataclass
class FakeOpikClient:
    """Captures the kwargs each searchable list endpoint receives."""

    traces: dict[str, Any] = field(default_factory=lambda: _page([]))
    spans: dict[str, Any] = field(default_factory=lambda: _page([]))
    threads: dict[str, Any] = field(default_factory=lambda: _page([]))
    experiments: dict[str, Any] = field(default_factory=lambda: _page([]))
    projects: dict[str, Any] = field(default_factory=lambda: _page([]))
    last_kwargs: dict[str, Any] = field(default_factory=dict)

    async def list_traces(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.traces

    async def list_spans(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.spans

    async def list_threads(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.threads

    async def list_experiments(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.experiments

    async def list_projects(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        return self.projects

    async def list_prompts(self, **kw: Any) -> dict[str, Any]:
        return _page([])

    async def list_test_suites(self, **kw: Any) -> dict[str, Any]:
        return _page([])

    async def list_test_suite_items(self, test_suite_id: str, **kw: Any) -> dict[str, Any]:
        return _page([])

    async def list_prompt_versions(self, prompt_id: str, **kw: Any) -> dict[str, Any]:
        return _page([])


def _sent_filters(fake: FakeOpikClient) -> list[dict[str, str]]:
    raw = fake.last_kwargs.get("filters")
    assert isinstance(raw, str), f"filters not sent as a JSON string: {fake.last_kwargs!r}"
    parsed: list[dict[str, str]] = json.loads(raw)
    return parsed


SDK_SOURCE = {"field": "source", "operator": "=", "key": "", "value": "sdk"}


# --- filters reach the backend ------------------------------------------- #


@pytest.mark.anyio
async def test_trace_filters_compile_to_the_backend_array_plus_sdk_source() -> None:
    fake = FakeOpikClient()
    await run_list(
        "trace",
        project_name="demo",
        filters="error_info is_not_empty AND duration > 5000",
        client=fake,
    )
    assert _sent_filters(fake) == [
        {"field": "error_info", "operator": "is_not_empty", "key": "", "value": ""},
        {"field": "duration", "operator": ">", "key": "", "value": "5000"},
        SDK_SOURCE,
    ]


@pytest.mark.anyio
async def test_trace_list_without_filters_still_hides_non_sdk_sources() -> None:
    """Mirrors the UI's Logs page: evaluator/playground/experiment traces are
    not application traffic and would otherwise dominate a triage list."""
    fake = FakeOpikClient()
    await run_list("trace", project_id="p-1", client=fake)
    assert _sent_filters(fake) == [SDK_SOURCE]
    assert fake.last_kwargs.get("truncate") is True


@pytest.mark.anyio
async def test_naming_source_disables_the_default() -> None:
    fake = FakeOpikClient()
    await run_list("trace", project_id="p-1", filters='source = "evaluator"', client=fake)
    assert _sent_filters(fake) == [
        {"field": "source", "operator": "=", "key": "", "value": "evaluator"}
    ]


@pytest.mark.anyio
async def test_header_echoes_the_applied_filter_including_the_default() -> None:
    fake = FakeOpikClient(traces=_page([{"id": "t-1", "name": "chat"}]))
    out = await run_list("trace", project_id="p-1", filters="duration > 5000", client=fake)
    first_line = out.splitlines()[0]
    assert first_line == '[list: trace | filters: duration > 5000 AND source = "sdk"]'


@pytest.mark.anyio
async def test_header_shows_the_default_source_alone_when_nothing_was_asked() -> None:
    fake = FakeOpikClient(traces=_page([{"id": "t-1", "name": "chat"}]))
    out = await run_list("trace", project_id="p-1", client=fake)
    assert out.splitlines()[0] == '[list: trace | filters: source = "sdk"]'


# --- errors -------------------------------------------------------------- #


@pytest.mark.anyio
async def test_invalid_filters_surface_the_self_healing_message() -> None:
    with pytest.raises(ToolError) as ei:
        await run_list("trace", project_id="p-1", filters="durration > 5", client=FakeOpikClient())
    assert "Did you mean 'duration'?" in str(ei.value)
    assert isinstance(ei.value.__cause__, OQLError)


@pytest.mark.anyio
async def test_filters_on_an_unsupported_type_name_the_supported_ones() -> None:
    with pytest.raises(ToolError, match="Filterable types: trace, span, thread, experiment"):
        await run_list("project", filters='name = "x"', client=FakeOpikClient())


# --- default columns ----------------------------------------------------- #


@pytest.mark.anyio
async def test_trace_rows_carry_triage_columns_by_default() -> None:
    fake = FakeOpikClient(
        traces=_page(
            [
                {
                    "id": "t-1",
                    "name": "chat",
                    "start_time": "2026-09-08T10:00:00Z",
                    "end_time": "2026-09-08T10:00:07Z",
                    "duration": 7123.5,
                    "total_estimated_cost": 0.0042,
                    "error_info": {"exception_type": "TimeoutError", "message": "upstream"},
                },
                {"id": "t-2", "name": "chat", "duration": 120},
            ]
        )
    )
    out = await run_list("trace", project_id="p-1", client=fake)
    assert "id | name | start_time | duration | error_type | total_estimated_cost" in out
    assert "t-1 | chat | 2026-09-08T10:00:00Z | 7123.5 | TimeoutError | 0.0042" in out
    assert "t-2 | chat |  | 120 |  | " in out
