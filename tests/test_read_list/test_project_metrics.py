"""``list('project_metric', …)`` — the time-series surface.

Its own file because it is its own runner: the collection path's page, size,
sort and per-entity filter tables do not apply to a series, and the list tool
delegates it whole rather than growing special cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.project_metrics import MAX_BUCKETS, METRICS, bucket_count
from opik_mcp.writes.schema_tool import run_schema

PROJECT = "01a08666-e863-76e8-809c-057f4aa151bc"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _series(name: str, values: list[float | None], *, start: str = "2026-09-02") -> dict[str, Any]:
    day = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    return {
        "name": name,
        "data": [
            {"time": (day + timedelta(days=i)).isoformat().replace("+00:00", "Z"), "value": v}
            for i, v in enumerate(values)
        ],
    }


@dataclass
class FakeOpikClient:
    """Only the metric endpoint, plus the project lookup the runner may need."""

    results: list[dict[str, Any]] = field(default_factory=list)
    projects: dict[str, Any] = field(default_factory=lambda: {"content": [], "total": 0})
    last_body: dict[str, Any] = field(default_factory=dict)
    calls: int = 0
    _base_url: str | None = None
    _workspace: str | None = None
    _api_key: str | None = None

    async def get_project_metrics(self, project_id: str, /, **body: Any) -> dict[str, Any]:
        self.calls += 1
        self.last_body = {"project_id": project_id, **body}
        return {"results": self.results}

    async def list_projects(self, **_kw: Any) -> dict[str, Any]:
        return self.projects


def _fake(**kw: Any) -> Any:
    # The backend names the series, and its name is its own ("traces" for
    # TRACE_COUNT, a score's name for FEEDBACK_SCORES) — not the metric_type
    # the caller asked for. The header carries that.
    kw.setdefault("results", [_series("traces", [1, 2, 0, 5, 3, 0, 8, 4])])
    return FakeOpikClient(**kw)


def _table(out: str) -> list[str]:
    return out.splitlines()[1:]


# --- the answer ----------------------------------------------------------- #


@pytest.mark.anyio
async def test_series_renders_as_a_table_of_time_buckets() -> None:
    """A table, not nested JSON: the same 30-day ten-group series measured
    3,649 tokens as JSON and 871 as a table."""
    out = await run_list(
        "project_metric", project_id=PROJECT, metric_type="trace_count", client=_fake()
    )
    rows = _table(out)
    assert rows[0] == "time | traces"
    assert rows[1] == "2026-09-02 | 1"
    assert len(rows) == 9, "a header and one row per bucket"


@pytest.mark.anyio
async def test_integers_are_not_printed_as_floats() -> None:
    """The backend answers every count as a float. Printing 1204.0 spends four
    characters a row saying nothing and reads as a measurement precision the
    number does not have."""
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_cost",
        client=_fake(results=[_series("cost", [0.0, 1.5, 12.0])]),
    )
    assert _table(out)[1:] == ["2026-09-02 | 0", "2026-09-03 | 1.5", "2026-09-04 | 12"]


@pytest.mark.anyio
async def test_an_empty_series_says_so_rather_than_printing_a_bare_header() -> None:
    out = await run_list(
        "project_metric", project_id=PROJECT, metric_type="trace_count", client=_fake(results=[])
    )
    assert "No trace_count data in this window." in out


@pytest.mark.anyio
async def test_hourly_buckets_are_labelled_with_the_hour() -> None:
    """A daily bucket labelled with a time implies a precision it lacks, and
    costs eleven characters a row to imply it; an hourly one needs it."""
    daily = await run_list(
        "project_metric", project_id=PROJECT, metric_type="trace_count", client=_fake()
    )
    hourly = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_count",
        interval="hourly",
        client=_fake(),
    )
    assert _table(daily)[1].startswith("2026-09-02 |")
    assert _table(hourly)[1].startswith("2026-09-02 00:00 |")


# --- what was applied ----------------------------------------------------- #


@pytest.mark.anyio
async def test_the_first_line_echoes_metric_interval_window_and_source() -> None:
    """A defaulted interval, a defaulted window and a defaulted source filter
    are three things the caller did not say; the echo is what stops the answer
    being read as the answer to a different question."""
    out = await run_list(
        "project_metric", project_id=PROJECT, metric_type="trace_count", client=_fake()
    )
    header = out.splitlines()[0]
    assert header.startswith("[list: project_metric | trace_count | daily | ")
    # The defaulted source shows up as part of the filter, once — not also as
    # a separate "source defaulted" clause the agent has to reconcile with it.
    assert header.count("sdk") == 1
    assert 'filters: source = "sdk"' in header


@pytest.mark.anyio
async def test_the_window_defaults_to_the_last_seven_days() -> None:
    fake = _fake()
    await run_list("project_metric", project_id=PROJECT, metric_type="trace_count", client=fake)
    start = datetime.fromisoformat(fake.last_body["interval_start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(fake.last_body["interval_end"].replace("Z", "+00:00"))
    assert (end - start) == timedelta(days=7)
    assert end <= datetime.now(UTC) + timedelta(seconds=5)
    assert fake.last_body["interval"] == "DAILY"


# --- filters ride the metric's own entity --------------------------------- #


@pytest.mark.anyio
async def test_a_span_metric_is_filtered_by_span_fields_in_the_span_array() -> None:
    """The backend applies each filter array to its own entity, so a span
    filter in the trace array silently filters nothing. Which array to use
    follows from the metric, not from the entity_type in the call."""
    fake = _fake()
    await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_count",
        filters='model = "gpt-4o"',
        client=fake,
    )
    assert fake.last_body["span_filters"] == [
        {"field": "model", "operator": "=", "key": "", "value": "gpt-4o"},
        {"field": "source", "operator": "=", "key": "", "value": "sdk"},
    ]
    assert "trace_filters" not in fake.last_body


@pytest.mark.anyio
async def test_a_field_the_metric_s_entity_does_not_have_is_refused_locally() -> None:
    """`model` is a span field. Asked of a trace metric the backend answers
    `Invalid filters query parameter` and names neither the field nor the valid
    ones, so the local check is the only usable error."""
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            filters='model = "gpt-4o"',
            client=_fake(),
        )
    assert "model" in str(exc.value)
    assert _fake().calls == 0


@pytest.mark.anyio
async def test_an_explicit_source_is_not_overridden() -> None:
    fake = _fake()
    await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_count",
        filters='source = "experiment"',
        client=fake,
    )
    sources = [c["value"] for c in fake.last_body["trace_filters"] if c["field"] == "source"]
    assert sources == ["experiment"]


# --- size is bounded before the call -------------------------------------- #


def test_bucket_arithmetic_matches_the_backend() -> None:
    """Verified live: a daily week is 8 rows, an hourly week 169, TOTAL 1."""
    week = ("2026-09-02T00:00:00Z", "2026-09-09T00:00:00Z")
    assert bucket_count("daily", *week) == 8
    assert bucket_count("hourly", *week) == 169
    assert bucket_count("weekly", *week) == 2
    assert bucket_count("total", *week) == 1


@pytest.mark.anyio
async def test_an_oversized_request_is_refused_without_calling_the_backend() -> None:
    """An hourly month is 721 buckets — an answer that costs more context than
    it can inform, and the user pays for it. Refused before the call, so it
    costs nothing at all."""
    fake = _fake()
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            interval="hourly",
            since="30d",
            client=fake,
        )
    message = str(exc.value)
    assert "721 time buckets" in message
    assert str(MAX_BUCKETS) in message
    assert fake.calls == 0, "nothing was asked of the backend"


@pytest.mark.anyio
async def test_the_refusal_names_requests_that_would_fit() -> None:
    """ "Too big" leaves the agent guessing which of three knobs to turn. These
    are the turns that actually fit, with the row count each produces."""
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            interval="hourly",
            since="30d",
            client=_fake(),
        )
    message = str(exc.value)
    assert "interval='daily' → 31 rows" in message
    assert "interval='total' → 1 row" in message
    assert "at interval='hourly'" in message


# --- the collection arguments do not apply -------------------------------- #


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"page": 2}, "page"),
        ({"size": 50}, "size"),
        ({"sort": "value desc"}, "sort"),
    ],
)
async def test_collection_arguments_are_refused_not_ignored(
    kwargs: dict[str, Any], named: str
) -> None:
    """Ignoring them silently would let an agent believe it had paged through a
    series it re-read from the start, or ordered rows ordered by time already."""
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            client=_fake(),
            **kwargs,
        )
    assert named in str(exc.value)
    assert "time buckets" in str(exc.value)


@pytest.mark.anyio
async def test_the_defaults_of_page_and_size_are_not_mistaken_for_a_choice() -> None:
    """They carry non-None defaults, so only a value the caller actually chose
    can be refused; the defaults arriving here are indistinguishable from
    absence and must not fail the call."""
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_count",
        page=1,
        size=25,
        client=_fake(),
    )
    assert "time | traces" in out


# --- arguments -------------------------------------------------------------#


@pytest.mark.anyio
async def test_metric_type_is_required_and_the_refusal_lists_the_choices() -> None:
    with pytest.raises(ToolError) as exc:
        await run_list("project_metric", project_id=PROJECT, client=_fake())
    message = str(exc.value)
    assert "needs metric_type" in message
    assert "trace_count" in message
    assert "span_cost" in message


@pytest.mark.anyio
async def test_an_unknown_metric_names_the_choices() -> None:
    with pytest.raises(ToolError, match="Unknown metric_type"):
        await run_list("project_metric", project_id=PROJECT, metric_type="latency", client=_fake())


@pytest.mark.anyio
async def test_an_unknown_interval_names_the_choices() -> None:
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            interval="minutely",
            client=_fake(),
        )
    assert "hourly" in str(exc.value)


@pytest.mark.anyio
async def test_project_scope_is_required() -> None:
    with pytest.raises(ToolError, match="project_id"):
        await run_list("project_metric", metric_type="trace_count", client=_fake())


@pytest.mark.anyio
async def test_a_project_name_resolves_to_its_id() -> None:
    fake = _fake(projects={"content": [{"id": PROJECT, "name": "demo"}], "total": 1})
    await run_list("project_metric", project_name="demo", metric_type="trace_count", client=fake)
    assert fake.last_body["project_id"] == PROJECT


# --- the reference -------------------------------------------------------- #


def test_the_schema_reference_carries_the_metric_table() -> None:
    """The table lives in schema(), not in the tool description: the
    description is billed on every request the host makes, this only when
    asked for."""
    reference = run_schema("list.project_metric")
    assert set(reference["metric_types"]) == set(METRICS)
    assert reference["metric_types"]["span_cost"]["about"] == "span"
    assert reference["metric_types"]["trace_error_rate"]["unit"] == "%"
    assert reference["limits"]["buckets"] == MAX_BUCKETS
    assert "page, size" in reference["not_supported"]
