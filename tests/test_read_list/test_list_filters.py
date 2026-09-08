"""``list`` tool — filters, sort, time window and search (OPIK-8283).

Everything here is observed through ``run_list`` with a fake client: the
arguments that reach the backend, the table text, and the error text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    assert "id | name | type | trace_id | duration | model | error_type" in out
    assert "s-1 | openai.chat | llm | t-1 | 812.0 | gpt-4o | RateLimitError" in out


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
        '[list: trace | filters: source = "sdk" | since: 2026-09-08T00:00:00Z'
        " | until: 2026-09-08T12:00:00Z]"
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
    assert "search" not in fake.last_kwargs
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
                    "status": "inactive",
                    "number_of_messages": 12,
                    "duration": 91000.0,
                    "last_updated_at": "2026-09-08T09:00:00Z",
                }
            ]
        )
    )
    out = await run_list("thread", project_id="p-1", client=fake)
    assert "id | status | number_of_messages | duration | last_updated_at" in out
    assert "th-1 | inactive | 12 | 91000.0 | 2026-09-08T09:00:00Z" in out


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
