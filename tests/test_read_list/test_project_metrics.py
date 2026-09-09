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
from opik_mcp.read_list.project_metrics import (
    MAX_BUCKETS,
    MAX_SERIES,
    METRICS,
    bucket_count,
    groupable_by,
)
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


@pytest.mark.anyio
async def test_every_row_count_in_a_refusal_is_the_real_one() -> None:
    """Found by review: the narrowed-window suggestion quoted MAX_BUCKETS
    rather than the count that window actually produces — "200 rows" for a
    request returning 193. Numbers in a refusal get repeated to a person."""
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            interval="hourly",
            since="2026-08-09T00:00:00Z",
            until="2026-09-09T00:00:00Z",
            client=_fake(),
        )
    message = str(exc.value)
    # The first line also carries a "→" (the window it refused), so match the
    # indented alternatives by their own shape rather than by the arrow.
    alternatives = [
        line.strip()
        for line in message.splitlines()
        if line.startswith("  ") and line.strip().startswith(("interval=", "since="))
    ]
    assert len(alternatives) == 4
    for line in alternatives:
        claim = int(line.split("→")[1].split()[0])
        if line.startswith("interval="):
            name = line.split("'")[1]
            actual = bucket_count(name, "2026-08-09T00:00:00Z", "2026-09-09T00:00:00Z")
        else:
            days = int(line.split("'")[1].rstrip("d"))
            start = f"2026-09-{9 - days:02d}T00:00:00Z"
            actual = bucket_count("hourly", start, "2026-09-09T00:00:00Z")
        assert claim == actual, f"{line!r} claims {claim}, really {actual}"


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


# --- grouping ------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_span_metric_groups_by_model() -> None:
    """ "Cost went up" is half an answer; "cost went up on one model" is the
    whole one."""
    fake = _fake(results=[_series("gpt-4o", [1.0, 2.0]), _series("claude-opus-4", [0.5, 0.4])])
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_count",
        breakdown="model",
        client=fake,
    )
    assert fake.last_body["breakdown"] == {"field": "MODEL"}
    assert _table(out)[0] == "time | gpt-4o | claude-opus-4"
    assert "by model" in out.splitlines()[0]


@pytest.mark.anyio
async def test_grouping_by_a_metadata_key_takes_the_key_inline() -> None:
    """One argument, not two: a key that only makes sense with a field is two
    chances to pass one without the other."""
    fake = _fake()
    await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_count",
        breakdown="metadata.environment",
        client=fake,
    )
    assert fake.last_body["breakdown"] == {"field": "METADATA", "metadata_key": "environment"}


@pytest.mark.anyio
async def test_metadata_without_a_key_says_which_form_to_use() -> None:
    with pytest.raises(ToolError, match=r"metadata\.<key>"):
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            breakdown="metadata",
            client=_fake(),
        )


@pytest.mark.anyio
async def test_a_metric_that_cannot_be_grouped_at_all_says_so_plainly() -> None:
    """Seven of the twenty are missing from the backend's own compatibility
    sets, and it answers them with a message that contradicts itself — "this
    field supports Span metrics only", about a span metric. Refused locally
    with the real reason, and without a round trip."""
    fake = _fake()
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="span_cost",
            breakdown="model",
            client=fake,
        )
    message = str(exc.value)
    assert "cannot be grouped at all" in message
    assert "span_cost" in message
    assert fake.calls == 0


@pytest.mark.anyio
async def test_a_grouping_the_metric_does_not_accept_lists_the_ones_it_does() -> None:
    fake = _fake()
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            breakdown="model",
            client=fake,
        )
    message = str(exc.value)
    assert "cannot be grouped by model" in message
    assert "tags" in message
    assert fake.calls == 0


@pytest.mark.anyio
async def test_a_refusal_points_at_a_metric_that_answers_the_same_question() -> None:
    """Per-model counts are a span question; `span_count` is where they live."""
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            breakdown="model",
            client=_fake(),
        )
    assert "metric_type='span_count'" in str(exc.value)


@pytest.mark.anyio
async def test_guardrail_grouping_belongs_to_one_metric_only() -> None:
    fake = _fake()
    await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="guardrails_failed_count",
        breakdown="guardrail_name",
        client=fake,
    )
    assert fake.last_body["breakdown"] == {"field": "GUARDRAIL_NAME"}

    with pytest.raises(ToolError, match="cannot be grouped by guardrail_name"):
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            breakdown="guardrail_name",
            client=_fake(),
        )


def test_the_ungroupable_metrics_are_exactly_the_ones_the_backend_omits() -> None:
    """Transcribed from BreakdownField's sets, so this pins the transcription
    against the seven found live rather than trusting it."""
    ungroupable = {name for name in METRICS if not groupable_by(name)}
    assert ungroupable == {
        "trace_average_duration",
        "trace_error_rate",
        "span_average_duration",
        "span_cost",
        "span_error_rate",
        "thread_average_duration",
        "thread_cost",
    }


# --- width is capped on the way out --------------------------------------- #


@pytest.mark.anyio
async def test_a_metric_that_fans_out_per_score_name_is_capped() -> None:
    """Feedback-score and token-usage metrics return one series per name with
    no grouping asked for, and nothing bounds that — sixty score names would
    be sixty columns. Unlike the bucket count this width is unknowable before
    the call, so it is capped on the way out."""
    many = [_series(f"score-{i:02d}", [float(i), float(i)]) for i in range(30)]
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_feedback_scores",
        client=_fake(results=many),
    )
    columns = _table(out)[0].split(" | ")
    assert len(columns) == MAX_SERIES + 1, "time plus the capped series"
    assert f"of {len(many)} series" in out


@pytest.mark.anyio
async def test_the_widest_series_are_the_ones_kept() -> None:
    """A change hides in the big series; keeping an arbitrary ten would as
    often as not drop the one the question was about."""
    results = [_series("tiny", [0.0, 0.1])] + [
        _series(f"big-{i}", [100.0 + i, 100.0]) for i in range(MAX_SERIES)
    ]
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_feedback_scores",
        client=_fake(results=results),
    )
    assert "tiny" not in _table(out)[0]


@pytest.mark.anyio
async def test_a_capped_score_metric_says_where_all_the_names_are() -> None:
    many = [_series(f"score-{i:02d}", [1.0]) for i in range(30)]
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_feedback_scores",
        client=_fake(results=many),
    )
    assert f"list('score_name', project_id='{PROJECT}')" in out


@pytest.mark.anyio
async def test_a_capped_grouped_metric_does_not_point_at_score_names() -> None:
    """Groups are not score names, and the backend already caps them at ten —
    naming a list of score names there would send the agent somewhere useless."""
    many = [_series(f"model-{i:02d}", [1.0]) for i in range(30)]
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_count",
        breakdown="model",
        client=_fake(results=many),
    )
    assert "score_name" not in out


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
