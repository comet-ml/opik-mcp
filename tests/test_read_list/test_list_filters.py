"""``list`` tool — filters, sort, time window and search (OPIK-8283).

Everything here is observed through ``run_list`` with a fake client: the
arguments that reach the backend, the table text, and the error text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.opik_client import OpikNotFoundError
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
    project_kwargs: dict[str, Any] = field(default_factory=dict)
    """``list_projects`` records separately: the tool also calls it on the side
    (project-name recovery, last-trace hint) after the main list call."""

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
        self.project_kwargs = kw
        return self.projects

    async def list_prompts(self, **kw: Any) -> dict[str, Any]:
        return _page([])

    async def list_test_suites(self, **kw: Any) -> dict[str, Any]:
        return _page([])

    async def list_test_suite_items(self, test_suite_id: str, **kw: Any) -> dict[str, Any]:
        return _page([])

    async def list_prompt_versions(self, prompt_id: str, **kw: Any) -> dict[str, Any]:
        return _page([])

    async def list_agent_insights_issues(self, **kw: Any) -> dict[str, Any]:
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
async def test_empty_result_under_the_default_source_says_how_to_widen_it() -> None:
    """Seen live: a project holding only experiment traces answers 'No traces
    found' under the sdk default. The agent needs to learn why in that reply."""
    out = await run_list("trace", project_id="p-1", client=FakeOpikClient())
    assert out.splitlines()[0] == '[list: trace | filters: source = "sdk"]'
    assert "No traces found." in out
    assert 'source = "experiment"' in out and "evaluator" in out and "playground" in out


@pytest.mark.anyio
async def test_empty_result_with_an_explicit_source_carries_no_hint() -> None:
    out = await run_list(
        "trace", project_id="p-1", filters='source = "evaluator"', client=FakeOpikClient()
    )
    assert "No traces found." in out
    assert "playground" not in out


@pytest.mark.anyio
async def test_empty_result_under_the_agents_own_filters_carries_no_hint() -> None:
    """With filters of the agent's own, those are the likelier reason for an
    empty page; the source hint would point the wrong way."""
    out = await run_list("trace", project_id="p-1", filters="duration > 5", client=FakeOpikClient())
    assert "No traces found." in out and "playground" not in out


@pytest.mark.anyio
async def test_a_sort_does_not_suppress_the_source_hint() -> None:
    """Sorting changes order, not membership — the empty page still needs the why."""
    out = await run_list("trace", project_id="p-1", sort="duration desc", client=FakeOpikClient())
    assert "playground" in out


@pytest.mark.anyio
async def test_window_with_traffic_inside_it_points_at_the_source_default() -> None:
    """The project's last trace is inside the window yet the page is empty: the
    sdk default hid it (experiment/evaluator traces). Say so."""
    fake = FakeOpikClient(
        projects=_page(
            [{"id": "p-1", "name": "demo", "last_updated_trace_at": "2026-09-08T10:00:00Z"}]
        )
    )
    out = await run_list("trace", project_name="demo", since="2026-09-08T00:00:00Z", client=fake)
    assert "playground" in out and "before your window" not in out


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


@pytest.mark.anyio
async def test_empty_filters_on_an_unsupported_type_is_the_same_as_none() -> None:
    fake = FakeOpikClient(projects=_page([{"id": "p-1", "name": "demo"}]))
    out = await run_list("project", filters="", client=fake)
    assert "filters" not in fake.project_kwargs
    assert "truncate" not in fake.project_kwargs
    assert out.startswith("Found 1 projects")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query", "fragment"),
    [
        ("duration > five", "Syntax error at position"),
        ("metadata.env >= 5", "'>=' is not valid for 'metadata' (dictionary)"),
        ('start_time > "yesterday"', "ISO-8601 instant with a timezone"),
        ('durration > 5 AND error_info = "x"', "2. Operator '='"),
    ],
)
async def test_every_error_class_reaches_the_agent_through_the_tool(
    query: str, fragment: str
) -> None:
    with pytest.raises(ToolError) as ei:
        await run_list("trace", project_id="p-1", filters=query, client=FakeOpikClient())
    message = str(ei.value)
    assert message.startswith("Invalid filters for trace:")
    assert fragment in message
    assert 'schema("list.trace")' in message


@pytest.mark.anyio
async def test_backend_timeout_is_reported_with_a_way_out() -> None:
    """``httpx.ReadTimeout`` stringifies to '' — seen live when a free-text
    search took longer than the client timeout. The agent must get a message
    that says what happened and how to narrow the query."""

    class TimingOut(FakeOpikClient):
        async def list_traces(self, **kw: Any) -> dict[str, Any]:
            raise httpx.ReadTimeout("")

    with pytest.raises(ToolError) as ei:
        await run_list("trace", project_id="p-1", search="order-42", client=TimingOut())
    message = str(ei.value)
    assert "did not answer in time" in message
    assert "list(trace" in message
    assert "since" in message and "size" in message
    assert isinstance(ei.value.__cause__, httpx.ReadTimeout)


@pytest.mark.anyio
async def test_backend_unreachable_is_reported_with_the_reason() -> None:
    class Unreachable(FakeOpikClient):
        async def list_traces(self, **kw: Any) -> dict[str, Any]:
            raise httpx.ConnectError("nodename nor servname provided")

    with pytest.raises(ToolError, match=r"Could not reach Opik.*nodename nor servname"):
        await run_list("trace", project_id="p-1", client=Unreachable())


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
    assert "id | name | start_time | duration_ms | error_type | total_estimated_cost" in out
    assert "t-1 | chat | 2026-09-08T10:00:00Z | 7124 | TimeoutError | 0.0042" in out
    assert "t-2 | chat |  | 120 |  | " in out


@pytest.mark.anyio
async def test_cells_are_compact_seconds_whole_ms_and_plain_decimals() -> None:
    """Row density is paid on every page: microseconds, sub-millisecond
    durations and scientific notation cost tokens and tell the agent nothing."""
    fake = FakeOpikClient(
        traces=_page(
            [
                {
                    "id": "t-1",
                    "name": "completion",
                    "start_time": "2026-07-31T11:26:45.360141Z",
                    "duration": 82.461,
                    "total_estimated_cost": 1.35e-05,
                    "end_time": "2026-07-31T11:26:46.000+00:00",
                }
            ]
        )
    )
    out = await run_list("trace", project_id="p-1", sort="end_time", client=fake)
    assert "t-1 | completion | 2026-07-31T11:26:45Z | 82 | " in out
    assert "| 0.0000135 | 2026-07-31T11:26:46Z" in out
    assert "e-05" not in out


@pytest.mark.anyio
async def test_half_milliseconds_round_up_and_non_finite_values_render_empty() -> None:
    fake = FakeOpikClient(
        traces=_page(
            [
                {"id": "t-1", "name": "a", "duration": 82.5},
                {"id": "t-2", "name": "b", "duration": float("nan")},
                {"id": "t-3", "name": "c", "duration": float("inf"), "total_estimated_cost": -0.5},
            ]
        )
    )
    out = await run_list("trace", project_id="p-1", client=fake)
    assert "t-1 | a |  | 83 |  | " in out
    assert "t-2 | b |  |  |  | " in out
    assert "t-3 | c |  |  |  | -0.5" in out


@pytest.mark.anyio
async def test_a_404_that_is_not_about_the_project_keeps_the_backend_message() -> None:
    class SomethingElseMissing(FakeOpikClient):
        async def list_traces(self, **kw: Any) -> dict[str, Any]:
            raise OpikNotFoundError("traces not found (404). — Workspace 'ws' not found")

    with pytest.raises(ToolError, match=r"Failed to list traces: .*Workspace 'ws' not found"):
        await run_list("trace", project_name="demo", client=SomethingElseMissing())


@pytest.mark.anyio
async def test_dynamic_duration_columns_carry_the_ms_label() -> None:
    fake = FakeOpikClient(spans=_page([{"id": "s-1", "name": "llm", "ttft": 412.9}]))
    out = await run_list("span", project_id="p-1", sort="ttft desc", client=fake)
    header = out.splitlines()[3]
    assert header.endswith("| error_type | ttft_ms")
    assert "| 413" in out
    # The field keeps its backend name in the header echo and in the request.
    assert "sort: ttft desc" in out.splitlines()[0]
    assert json.loads(fake.last_kwargs["sorting"])[0]["field"] == "ttft"


@pytest.mark.anyio
async def test_project_rows_show_when_the_last_trace_landed() -> None:
    fake = FakeOpikClient(
        projects=_page(
            [
                {
                    "id": "p-1",
                    "name": "demo",
                    "created_at": "2026-01-01T00:00:00Z",
                    "last_updated_trace_at": "2026-09-08T09:15:22.123456Z",
                },
                {"id": "p-2", "name": "empty", "created_at": "2026-02-01T00:00:00Z"},
            ]
        )
    )
    out = await run_list("project", client=fake)
    assert "id | name | created_at | last_updated_trace_at" in out
    assert "p-1 | demo | 2026-01-01T00:00:00Z | 2026-09-08T09:15:22Z" in out
    assert "p-2 | empty | 2026-02-01T00:00:00Z | " in out


# --- span: project-wide search --------------------------------------------- #


@pytest.mark.anyio
async def test_span_list_requires_project_scope() -> None:
    with pytest.raises(ToolError, match=r"list\('span'\) requires project_id \(or project_name\)"):
        await run_list("span", client=FakeOpikClient())


@pytest.mark.anyio
async def test_span_filters_search_the_whole_project() -> None:
    fake = FakeOpikClient()
    await run_list(
        "span",
        project_name="demo",
        filters='type = "llm" AND usage.total_tokens > 10000',
        client=fake,
    )
    assert fake.last_kwargs.get("project_name") == "demo"
    assert "trace_id" not in fake.last_kwargs
    assert _sent_filters(fake) == [
        {"field": "type", "operator": "=", "key": "", "value": "llm"},
        {"field": "usage.total_tokens", "operator": ">", "key": "", "value": "10000"},
        SDK_SOURCE,
    ]


@pytest.mark.anyio
async def test_span_rejects_trace_only_fields_with_the_span_field_list() -> None:
    with pytest.raises(ToolError) as ei:
        await run_list("span", project_id="p-1", filters='thread_id = "t"', client=FakeOpikClient())
    message = str(ei.value)
    assert "Unknown field 'thread_id'" in message
    assert "provider" in message and "llm_span_count" not in message


@pytest.mark.anyio
async def test_span_rows_carry_span_columns_by_default() -> None:
    fake = FakeOpikClient(
        spans=_page(
            [
                {
                    "id": "s-1",
                    "name": "openai.chat",
                    "type": "llm",
                    "trace_id": "t-1",
                    "duration": 812.0,
                    "model": "gpt-4o",
                    "error_info": {"exception_type": "RateLimitError"},
                }
            ]
        )
    )
    out = await run_list("span", project_id="p-1", client=fake)
    assert "id | name | type | trace_id | duration_ms | model | error_type" in out
    assert "s-1 | openai.chat | llm | t-1 | 812 | gpt-4o | RateLimitError" in out


# --- since / until window, search ------------------------------------------- #


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.mark.anyio
@pytest.mark.parametrize(("shorthand", "delta"), [("1h", 3600), ("30m", 1800), ("7d", 7 * 86400)])
async def test_relative_since_resolves_against_now(shorthand: str, delta: int) -> None:
    fake = FakeOpikClient()
    before = datetime.now(UTC)
    await run_list("trace", project_id="p-1", since=shorthand, client=fake)
    sent = fake.last_kwargs["from_time"]
    assert sent.endswith("Z")
    assert abs((before - _instant(sent)).total_seconds() - delta) < 5
    assert "to_time" not in fake.last_kwargs


@pytest.mark.anyio
async def test_relative_since_is_echoed_as_written_plus_the_bound() -> None:
    fake = FakeOpikClient(traces=_page([{"id": "t-1", "name": "chat"}]))
    out = await run_list("trace", project_id="p-1", since="30d", client=fake)
    assert re.search(r"\| since: 30d \(\d{4}-\d\d-\d\dT\d\d:\d\dZ\)\]$", out.splitlines()[0])


@pytest.mark.anyio
async def test_empty_windowed_page_reports_the_projects_last_trace() -> None:
    """Seen live: since="30d" on a project whose traffic ended weeks earlier
    looks the same as a project with no traffic. One project read tells them apart."""
    fake = FakeOpikClient(
        projects=_page(
            [{"id": "p-1", "name": "demo", "last_updated_trace_at": "2026-07-31T11:26:45.360141Z"}]
        )
    )
    out = await run_list("trace", project_name="demo", since="2026-08-09T00:00:00Z", client=fake)
    assert "No traces found." in out
    assert "Last trace in this project: 2026-07-31T11:26Z, before your window." in out
    assert fake.project_kwargs.get("name") == "demo"


@pytest.mark.anyio
async def test_empty_windowed_page_on_a_project_without_traces_says_so() -> None:
    fake = FakeOpikClient(projects=_page([{"id": "p-1", "name": "demo"}]))
    out = await run_list("trace", project_id="p-1", since="1h", client=fake)
    assert "This project has no traces yet." in out


@pytest.mark.anyio
async def test_empty_windowed_page_with_traffic_inside_the_window_adds_nothing() -> None:
    fake = FakeOpikClient(
        projects=_page(
            [{"id": "p-1", "name": "demo", "last_updated_trace_at": "2026-09-08T10:00:00Z"}]
        )
    )
    out = await run_list(
        "trace",
        project_name="demo",
        since="2026-09-08T00:00:00Z",
        filters="duration > 5",
        client=fake,
    )
    assert out.endswith("No traces found.")


@pytest.mark.anyio
async def test_unknown_project_name_suggests_the_closest_one() -> None:
    class NoSuchProject(FakeOpikClient):
        async def list_traces(self, **kw: Any) -> dict[str, Any]:
            raise OpikNotFoundError(
                "traces not found (404). — Project name: Defualt Project not found"
            )

    fake = NoSuchProject(
        projects=_page([{"id": "p-1", "name": "Default Project"}, {"id": "p-2", "name": "probe"}])
    )
    with pytest.raises(ToolError) as ei:
        await run_list("trace", project_name="Defualt Project", client=fake)
    message = str(ei.value)
    assert "Project 'Defualt Project' not found." in message
    assert "Did you mean 'Default Project'?" in message
    assert "Projects: Default Project, probe" in message
    assert isinstance(ei.value.__cause__, OpikNotFoundError)


@pytest.mark.anyio
async def test_search_calls_get_a_longer_client_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cold full-text search took 32 s live; other calls must keep the 30 s guard."""
    from opik_mcp.read_list import list_tool

    seen: list[float | None] = []

    def fake_factory(settings: Any, *, timeout: float | None = None) -> FakeOpikClient:
        seen.append(timeout)
        return FakeOpikClient()

    monkeypatch.setattr(list_tool, "make_opik_client", fake_factory)
    monkeypatch.setattr(list_tool, "get_settings", lambda: object())
    await run_list("trace", project_id="p-1", search="order-42")
    await run_list("trace", project_id="p-1")
    assert seen == [60.0, None]


@pytest.mark.anyio
async def test_iso_window_passes_through_and_is_echoed() -> None:
    fake = FakeOpikClient(traces=_page([{"id": "t-1", "name": "chat"}]))
    out = await run_list(
        "trace",
        project_id="p-1",
        since="2026-09-08T00:00:00Z",
        until="2026-09-08T12:00:00+00:00",
        client=fake,
    )
    assert fake.last_kwargs["from_time"] == "2026-09-08T00:00:00Z"
    assert fake.last_kwargs["to_time"] == "2026-09-08T12:00:00Z"
    assert out.splitlines()[0] == (
        '[list: trace | filters: source = "sdk" | since: 2026-09-08T00:00Z'
        " | until: 2026-09-08T12:00Z]"
    )


@pytest.mark.anyio
async def test_malformed_window_value_names_both_accepted_forms() -> None:
    with pytest.raises(ToolError) as ei:
        await run_list("trace", project_id="p-1", since="yesterday", client=FakeOpikClient())
    message = str(ei.value)
    assert "since" in message and "'yesterday'" in message
    assert "1h" in message and "2026-09-08T10:00:00Z" in message


@pytest.mark.anyio
async def test_until_before_since_is_rejected_locally() -> None:
    with pytest.raises(ToolError, match=r"until .* is before since"):
        await run_list(
            "trace",
            project_id="p-1",
            since="2026-09-08T12:00:00Z",
            until="2026-09-08T00:00:00Z",
            client=FakeOpikClient(),
        )


@pytest.mark.anyio
async def test_window_order_is_checked_on_instants_not_strings() -> None:
    """A sub-second ``until`` sorts before ``since`` as text ('.' < 'Z') but is
    later in time; the bounds must be compared as instants."""
    fake = FakeOpikClient()
    await run_list(
        "trace",
        project_id="p-1",
        since="2026-09-08T10:00:00Z",
        until="2026-09-08T10:00:00.500Z",
        client=fake,
    )
    assert fake.last_kwargs["to_time"] == "2026-09-08T10:00:00.500Z"


@pytest.mark.anyio
async def test_space_separated_date_is_rejected_before_the_backend_would() -> None:
    with pytest.raises(ToolError, match="Invalid since"):
        await run_list(
            "trace", project_id="p-1", since="2026-09-08 10:00:00Z", client=FakeOpikClient()
        )


@pytest.mark.anyio
async def test_window_on_experiment_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(ToolError, match="experiments have no time window"):
        await run_list("experiment", since="1h", client=FakeOpikClient())


@pytest.mark.anyio
async def test_window_on_thread_is_forwarded() -> None:
    fake = FakeOpikClient()
    await run_list("thread", project_id="p-1", since="2026-09-08T00:00:00Z", client=fake)
    assert fake.last_kwargs["from_time"] == "2026-09-08T00:00:00Z"


@pytest.mark.anyio
async def test_search_is_forwarded_for_spans_and_echoed() -> None:
    fake = FakeOpikClient(spans=_page([{"id": "s-1", "name": "tool"}]))
    out = await run_list("span", project_id="p-1", search="order-42", client=fake)
    assert fake.last_kwargs["search"] == "order-42"
    assert out.splitlines()[0] == '[list: span | filters: source = "sdk" | search: "order-42"]'


@pytest.mark.anyio
async def test_search_on_a_type_without_it_is_dropped_with_a_note() -> None:
    fake = FakeOpikClient(projects=_page([{"id": "p-1", "name": "demo"}]))
    out = await run_list("project", search="demo", client=fake)
    assert "search" not in fake.project_kwargs
    assert out.splitlines()[0] == "[list: project | search ignored (only trace, span, thread)]"


# --- sort ------------------------------------------------------------------ #


@pytest.mark.anyio
async def test_sort_reaches_the_backend_as_one_sorting_field() -> None:
    fake = FakeOpikClient()
    await run_list("trace", project_id="p-1", sort="duration desc", client=fake)
    assert json.loads(fake.last_kwargs["sorting"]) == [{"field": "duration", "direction": "DESC"}]


@pytest.mark.anyio
async def test_sort_direction_defaults_to_desc_and_is_case_insensitive() -> None:
    fake = FakeOpikClient()
    await run_list("trace", project_id="p-1", sort="total_estimated_cost", client=fake)
    assert json.loads(fake.last_kwargs["sorting"])[0]["direction"] == "DESC"
    await run_list("trace", project_id="p-1", sort="start_time ASC", client=fake)
    assert json.loads(fake.last_kwargs["sorting"])[0]["direction"] == "ASC"


@pytest.mark.anyio
async def test_sort_accepts_dynamic_score_and_usage_fields() -> None:
    fake = FakeOpikClient()
    await run_list("trace", project_id="p-1", sort="feedback_scores.accuracy asc", client=fake)
    assert json.loads(fake.last_kwargs["sorting"]) == [
        {"field": "feedback_scores.accuracy", "direction": "ASC"}
    ]
    await run_list("span", project_id="p-1", sort="usage.total_tokens", client=fake)
    assert json.loads(fake.last_kwargs["sorting"])[0]["field"] == "usage.total_tokens"


@pytest.mark.anyio
async def test_sort_on_an_unsupported_field_lists_the_sortable_ones() -> None:
    with pytest.raises(ToolError) as ei:
        await run_list("trace", project_id="p-1", sort="error_type", client=FakeOpikClient())
    message = str(ei.value)
    assert "'error_type' is not sortable for trace" in message
    assert "Sortable: " in message
    assert "duration" in message and "feedback_scores.<name>" in message


@pytest.mark.anyio
async def test_sort_with_a_bad_direction_names_the_accepted_forms() -> None:
    with pytest.raises(ToolError, match=r"<field> \[asc\|desc\]"):
        await run_list("trace", project_id="p-1", sort="duration sideways", client=FakeOpikClient())


@pytest.mark.anyio
async def test_sort_on_an_unsupported_type_names_the_supported_ones() -> None:
    with pytest.raises(ToolError, match="Sortable types: trace, span, thread, experiment"):
        await run_list("project", sort="name", client=FakeOpikClient())


@pytest.mark.anyio
async def test_header_echoes_the_sort_and_flags_a_dropped_one() -> None:
    honoured = FakeOpikClient(
        traces=_page([{"id": "t-1", "name": "chat"}], sortable_by=["duration", "start_time"])
    )
    out = await run_list("trace", project_id="p-1", sort="duration", client=honoured)
    assert out.splitlines()[0] == '[list: trace | filters: source = "sdk" | sort: duration desc]'

    dropped = FakeOpikClient(traces=_page([{"id": "t-1", "name": "chat"}], sortable_by=[]))
    out = await run_list("trace", project_id="p-1", sort="duration", client=dropped)
    assert (
        "sort: duration desc (dropped by the backend for this workspace size" in out.splitlines()[0]
    )


@pytest.mark.anyio
async def test_header_lists_filters_sort_window_search_in_that_order() -> None:
    fake = FakeOpikClient(traces=_page([{"id": "t-1", "name": "chat"}]))
    out = await run_list(
        "trace",
        project_id="p-1",
        filters="duration > 5000",
        sort="duration",
        since="2026-09-08T00:00:00Z",
        search="order-42",
        client=fake,
    )
    assert out.splitlines()[0] == (
        '[list: trace | filters: duration > 5000 AND source = "sdk" | sort: duration desc'
        ' | since: 2026-09-08T00:00Z | search: "order-42"]'
    )


# --- columns follow the sort and filters ------------------------------------ #


@pytest.mark.anyio
async def test_sort_field_becomes_a_column_resolved_from_the_usage_map() -> None:
    fake = FakeOpikClient(
        traces=_page(
            [
                {
                    "id": "t-1",
                    "name": "chat",
                    "usage": {"total_tokens": 4321, "prompt_tokens": 4000},
                },
                {"id": "t-2", "name": "chat"},
            ]
        )
    )
    out = await run_list("trace", project_id="p-1", sort="usage.total_tokens", client=fake)
    header = out.splitlines()[3]
    assert header == (
        "id | name | start_time | duration_ms | error_type | total_estimated_cost"
        " | usage.total_tokens"
    )
    assert "t-1 | chat |  |  |  |  | 4321" in out
    assert "t-2 | chat |  |  |  |  | " in out


@pytest.mark.anyio
async def test_filter_fields_become_columns_deduplicated_and_in_order() -> None:
    fake = FakeOpikClient(
        traces=_page(
            [
                {
                    "id": "t-1",
                    "name": "chat",
                    "duration": 9000,
                    "tags": ["prod", "beta"],
                    "metadata": {"environment": "staging", "region": "eu"},
                    "feedback_scores": [{"name": "accuracy", "value": 0.42}],
                }
            ]
        )
    )
    out = await run_list(
        "trace",
        project_id="p-1",
        filters=(
            'duration > 5000 AND tags contains "prod" AND metadata.environment = "staging" '
            "AND feedback_scores.accuracy < 0.5 AND tags is_not_empty"
        ),
        sort="feedback_scores.accuracy asc",
        client=fake,
    )
    header = out.splitlines()[3]
    assert header == (
        "id | name | start_time | duration_ms | error_type | total_estimated_cost"
        " | feedback_scores.accuracy | tags | metadata.environment"
    )
    assert "t-1 | chat |  | 9000 |  |  | 0.42 | ['prod', 'beta'] | staging" in out


@pytest.mark.anyio
async def test_body_and_source_fields_are_never_appended_as_columns() -> None:
    fake = FakeOpikClient(traces=_page([{"id": "t-1", "name": "chat"}]))
    out = await run_list(
        "trace",
        project_id="p-1",
        filters='input contains "hello" AND error_info is_not_empty AND source = "evaluator"',
        client=fake,
    )
    header = out.splitlines()[3]
    assert header == "id | name | start_time | duration_ms | error_type | total_estimated_cost"


@pytest.mark.anyio
async def test_dynamic_columns_still_truncate_long_values() -> None:
    fake = FakeOpikClient(
        spans=_page([{"id": "s-1", "name": "llm", "metadata": {"prompt": "x" * 200}}])
    )
    out = await run_list(
        "span", project_id="p-1", filters='metadata.prompt contains "x"', client=fake
    )
    assert "x" * 57 + "..." in out
    assert "x" * 58 not in out


# --- thread / experiment ---------------------------------------------------- #


@pytest.mark.anyio
async def test_thread_filters_reach_the_backend_with_the_sdk_default() -> None:
    fake = FakeOpikClient()
    await run_list(
        "thread",
        project_name="demo",
        filters=(
            'status = "active" AND number_of_messages > 20 AND feedback_scores.helpfulness < 0.5'
        ),
        client=fake,
    )
    assert _sent_filters(fake) == [
        {"field": "status", "operator": "=", "key": "", "value": "active"},
        {"field": "number_of_messages", "operator": ">", "key": "", "value": "20"},
        {"field": "feedback_scores", "operator": "<", "key": "helpfulness", "value": "0.5"},
        SDK_SOURCE,
    ]


@pytest.mark.anyio
async def test_experiment_filters_reach_the_backend_without_a_source_default() -> None:
    fake = FakeOpikClient()
    await run_list(
        "experiment",
        name="rerank",
        filters='dataset_id = "ds-1" AND tags contains "baseline"',
        client=fake,
    )
    assert fake.last_kwargs.get("name") == "rerank"
    assert _sent_filters(fake) == [
        {"field": "dataset_id", "operator": "=", "key": "", "value": "ds-1"},
        {"field": "tags", "operator": "contains", "key": "", "value": "baseline"},
    ]


@pytest.mark.anyio
async def test_experiment_list_without_filters_sends_none() -> None:
    fake = FakeOpikClient()
    out = await run_list("experiment", client=fake)
    assert "filters" not in fake.last_kwargs
    assert not out.startswith("[list:")


@pytest.mark.anyio
async def test_thread_and_experiment_unknown_fields_list_their_own_fields() -> None:
    with pytest.raises(ToolError) as ei:
        await run_list("thread", project_id="p-1", filters='model = "x"', client=FakeOpikClient())
    assert "first_message" in str(ei.value) and "model" in str(ei.value)

    with pytest.raises(ToolError) as ei:
        await run_list("experiment", filters="duration > 5", client=FakeOpikClient())
    assert "experiment_scores" in str(ei.value) and "Unknown field 'duration'" in str(ei.value)


@pytest.mark.anyio
async def test_thread_rows_carry_duration() -> None:
    fake = FakeOpikClient(
        threads=_page(
            [
                {
                    "id": "th-1",
                    "first_message": (
                        "Hi, my order #4411 never arrived and support is not answering"
                    ),
                    "status": "inactive",
                    "number_of_messages": 12,
                    "duration": 91000.0,
                    "last_updated_at": "2026-09-08T09:00:00Z",
                }
            ]
        )
    )
    out = await run_list("thread", project_id="p-1", client=fake)
    assert "id | first_message | status | number_of_messages | duration_ms | last_updated_at" in out
    assert (
        "th-1 | Hi, my order #4411 never arrived and support is not answe... | inactive | 12"
        " | 91000 | 2026-09-08T09:00:00Z"
    ) in out


@pytest.mark.anyio
async def test_experiment_rows_summarise_feedback_scores() -> None:
    fake = FakeOpikClient(
        experiments=_page(
            [
                {
                    "id": "e-1",
                    "name": "rerank-v2",
                    "dataset_name": "golden",
                    "created_at": "2026-09-01T00:00:00Z",
                    "feedback_scores": [
                        {"name": "accuracy", "value": 0.8125},
                        {"name": "hallucination", "value": 0.1},
                    ],
                }
            ]
        )
    )
    out = await run_list("experiment", client=fake)
    assert "id | name | dataset_name | created_at | feedback_scores" in out
    assert (
        "e-1 | rerank-v2 | golden | 2026-09-01T00:00:00Z | accuracy=0.8125, hallucination=0.1"
        in out
    )
