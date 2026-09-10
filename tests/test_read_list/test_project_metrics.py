"""``list('project_metric', …)`` — the time-series surface.

Its own file because it is its own runner: the collection path's page, size,
sort and per-entity filter tables do not apply to a series, and the list tool
delegates it whole rather than growing special cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
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
    # A rate or an average is charted with its entity's count alongside, so a
    # bucket with nothing in it can be told from a bucket that measured zero.
    # Both calls land here; these say what the second one answers.
    by_metric: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    fails: set[str] = field(default_factory=set)
    bodies: list[dict[str, Any]] = field(default_factory=list)
    # The names the project has recorded, for checking a `series` the caller
    # supplied. None means the endpoint itself fails.
    usage_names: list[str] | None = None
    score_names: list[str] | None = None
    name_lookups: int = 0
    _base_url: str | None = None
    _workspace: str | None = None
    _api_key: str | None = None

    async def list_project_token_usage_names(self, project_id: str, /) -> dict[str, Any]:
        self.name_lookups += 1
        if self.usage_names is None:
            raise httpx.ReadTimeout("usage names timed out")
        return {"names": self.usage_names}

    async def list_project_score_names(self, project_id: str, /) -> dict[str, Any]:
        self.name_lookups += 1
        if self.score_names is None:
            raise httpx.ReadTimeout("score names timed out")
        return {"scores": [{"name": name} for name in self.score_names]}

    async def get_project_metrics(self, project_id: str, /, **body: Any) -> dict[str, Any]:
        self.calls += 1
        self.last_body = {"project_id": project_id, **body}
        self.bodies.append(self.last_body)
        kind = str(body.get("metric_type"))
        if kind in self.fails:
            raise httpx.ReadTimeout("companion timed out")
        return {"results": self.by_metric.get(kind, self.results)}

    def body_for(self, metric_type: str) -> dict[str, Any]:
        """The request sent for one metric — two go out for a rate."""
        return next(b for b in self.bodies if b.get("metric_type") == metric_type)

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
async def test_an_all_zero_series_collapses_to_one_line() -> None:
    """Found by running the packaged server: a cost question on a project with
    no cost printed 31 rows of "| 0" for 148 tokens. The window is already on
    the header line, so the table adds nothing but its own length."""
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_cost",
        since="30d",
        client=_fake(results=[_series("cost", [0.0] * 31)]),
    )
    assert "every bucket is zero" in out
    assert "| 0" not in out
    assert len(out.splitlines()) == 2, "the header and one line"


@pytest.mark.anyio
async def test_one_non_zero_bucket_still_gets_the_table() -> None:
    """The collapse must not eat a real answer that happens to be mostly quiet
    — the one day something happened is the whole point of asking."""
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_cost",
        client=_fake(results=[_series("cost", [0.0, 0.0, 4.12, 0.0])]),
    )
    assert "2026-09-04 | 4.12" in out


@pytest.mark.anyio
async def test_a_total_row_is_labelled_with_the_window_not_its_first_day() -> None:
    """The backend labels its single TOTAL bucket with the window's start, so
    passing it through read as "on the 3rd there were 5" when the number is
    the whole week's."""
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_count",
        interval="total",
        since="2026-09-03T00:00:00Z",
        until="2026-09-10T00:00:00Z",
        client=_fake(results=[_series("traces", [5], start="2026-09-03")]),
    )
    assert "2026-09-03 → 2026-09-10 | 5" in out


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


# --- picking one of a metric's series ------------------------------------- #


@pytest.mark.anyio
async def test_grouping_a_duration_charts_one_percentile() -> None:
    """The backend cannot fan a metric out per percentile *and* group it, so
    it demands one — and used to be answerable only with a 422 the agent had
    no argument to act on."""
    fake = _fake()
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_duration",
        breakdown="name",
        series="p99",
        client=fake,
    )
    assert fake.last_body["breakdown"] == {"field": "NAME", "sub_metric": "p99"}
    assert "by name (p99)" in out.splitlines()[0]


@pytest.mark.anyio
async def test_a_grouped_duration_defaults_to_the_median_and_says_which() -> None:
    """A default keeps the common case to one call; echoing it keeps the
    number from being read as some other percentile."""
    fake = _fake()
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_duration",
        breakdown="model",
        client=fake,
    )
    assert fake.last_body["breakdown"]["sub_metric"] == "p50"
    assert "(p50)" in out.splitlines()[0]


@pytest.mark.anyio
async def test_a_grouped_token_metric_defaults_to_the_total() -> None:
    fake = _fake()
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_token_usage",
        breakdown="model",
        client=fake,
    )
    assert fake.last_body["breakdown"]["sub_metric"] == "total_tokens"
    assert "(total_tokens)" in out.splitlines()[0]


@pytest.mark.anyio
async def test_a_grouped_score_metric_asks_which_score_rather_than_choosing() -> None:
    """The names are the project's own — no one of them is the obvious
    subject, so choosing silently would answer a different question."""
    fake = _fake()
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="span_feedback_scores",
            breakdown="model",
            client=fake,
        )
    message = str(exc.value)
    assert "series=<score name>" in message
    assert "read('project'" in message
    assert fake.calls == 0


@pytest.mark.anyio
async def test_a_named_score_is_sent_as_the_sub_metric_verbatim() -> None:
    """Score names carry their own case and spacing."""
    fake = _fake()
    await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_feedback_scores",
        breakdown="tags",
        series="Answer Relevance",
        client=fake,
    )
    assert fake.last_body["breakdown"]["sub_metric"] == "Answer Relevance"


@pytest.mark.anyio
async def test_an_unknown_percentile_names_the_three_that_exist() -> None:
    fake = _fake()
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_duration",
            breakdown="name",
            series="p95",
            client=fake,
        )
    assert "p50, p90, p99" in str(exc.value)
    assert fake.calls == 0


@pytest.mark.anyio
async def test_series_without_a_breakdown_is_refused_not_ignored() -> None:
    """Ungrouped, every series comes back anyway. Accepting the argument and
    charting all of them would look like it had been honoured."""
    fake = _fake()
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_duration",
            series="p99",
            client=fake,
        )
    assert "already returns every series" in str(exc.value)
    assert fake.calls == 0


@pytest.mark.anyio
async def test_series_on_a_single_series_metric_is_refused() -> None:
    fake = _fake()
    with pytest.raises(ToolError, match="no sub-series"):
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            breakdown="tags",
            series="p99",
            client=fake,
        )
    assert fake.calls == 0


# --- a zero that is not a measurement ------------------------------------- #


@pytest.mark.anyio
async def test_a_bucket_with_no_traces_is_left_out_rather_than_charted_as_zero() -> None:
    """0% error looks like a perfect day and can mean an empty one. The
    project overview already refuses to print a rate over a period with no
    traces; a series is the same claim, one bucket at a time — and a row of
    empty cells costs as much to print as a real one."""
    fake = _fake(
        results=[_series("error_rate", [0, 20, 0])],
        by_metric={"TRACE_COUNT": [_series("traces", [0, 5, 3])]},
    )
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_error_rate",
        client=fake,
    )
    rows = _table(out)
    assert "2026-09-02" not in out, "no traces that day, so no rate to report"
    assert rows[1] == "2026-09-03 | 20"
    assert rows[2] == "2026-09-04 | 0", "traces and no errors — a real zero, kept"
    assert "1 of 3 buckets are not listed: no traces in them" in out


@pytest.mark.anyio
async def test_the_companion_count_is_filtered_like_the_metric() -> None:
    """A rate over SDK traffic compared against every trace would blank the
    wrong buckets."""
    fake = _fake(
        results=[_series("error_rate", [0])],
        by_metric={"TRACE_COUNT": [_series("traces", [1])]},
    )
    await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_error_rate",
        filters='tags contains "prod"',
        client=fake,
    )
    counted = fake.body_for("TRACE_COUNT")["trace_filters"]
    assert counted == fake.body_for("TRACE_ERROR_RATE")["trace_filters"]


@pytest.mark.anyio
async def test_a_window_with_nothing_in_it_says_so_instead_of_a_column_of_zeros() -> None:
    fake = _fake(
        results=[_series("error_rate", [0, 0, 0])],
        by_metric={"TRACE_COUNT": [_series("traces", [0, 0, 0])]},
    )
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_error_rate",
        client=fake,
    )
    assert "No traces in this window" in out
    assert "2026-09-02" not in out


@pytest.mark.anyio
async def test_the_chart_survives_the_companion_count_failing() -> None:
    """An ambiguous zero beats no answer: the note is dropped, not the rows."""
    fake = _fake(results=[_series("error_rate", [0, 20])], fails={"TRACE_COUNT"})
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_error_rate",
        client=fake,
    )
    assert _table(out)[1] == "2026-09-02 | 0"
    assert "not listed" not in out


@pytest.mark.anyio
async def test_a_count_metric_is_not_charged_a_second_call() -> None:
    """Only a rate and an average have an ambiguous zero. A count of 0 means
    zero, and paying 259 ms to confirm it would be the user's money."""
    fake = _fake()
    await run_list("project_metric", project_id=PROJECT, metric_type="trace_count", client=fake)
    assert fake.calls == 1


# --- refusals that end the search ----------------------------------------- #


@pytest.mark.anyio
async def test_a_grouping_no_metric_of_that_kind_supports_says_so() -> None:
    """Told only that `trace_cost` cannot be grouped by model, the next move
    is `span_cost` — which is in the backend's omitted set and refuses too.
    Two round trips to learn one fact, so the first refusal answers the
    follow-up."""
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_cost",
            breakdown="model",
            client=_fake(),
        )
    message = str(exc.value)
    assert "No cost metric can be grouped by model" in message
    assert "nothing to retry" in message
    assert "span_count" in message, "and where the field is accepted"


@pytest.mark.anyio
async def test_a_bucket_the_backend_reported_as_null_is_not_listed() -> None:
    """A null is the backend saying it has nothing for that bucket, and it
    prints as a row of separators. Seen live: nine days of three percentile
    columns, one day of numbers, eight rows of " |  | "."""
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="trace_duration",
        client=_fake(
            results=[
                _series("duration.p50", [None, 16.178, None]),
                _series("duration.p99", [None, 20.0, None]),
            ]
        ),
    )
    rows = _table(out)
    assert rows[0] == "time | duration.p50 | duration.p99"
    assert rows[1] == "2026-09-03 | 16.178 | 20"
    assert len(rows) == 4, "one header, one row, and the note — not two null rows"
    assert "2 of 3 buckets are not listed: no trace_duration recorded" in out


@pytest.mark.anyio
async def test_a_zero_is_kept_where_a_null_is_dropped() -> None:
    """They are different claims: 0 spans is a measurement, no data is not."""
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_count",
        client=_fake(results=[_series("spans", [0, None, 4])]),
    )
    assert _table(out)[1:3] == ["2026-09-02 | 0", "2026-09-04 | 4"]
    assert "1 of 3 buckets are not listed: no span_count recorded" in out


# --- checking the series against the project ------------------------------ #


def _known(**kw: Any) -> Any:
    """A fake that also answers the two name endpoints."""
    fake = _fake(**kw)
    fake.usage_names = ["total_tokens", "prompt_tokens", "completion_tokens"]
    fake.score_names = ["Hallucination", "Answer Relevance"]
    return fake


@pytest.mark.anyio
async def test_an_unrecorded_usage_key_is_named_as_the_problem() -> None:
    """The backend does not refuse an unknown sub-metric — it charts nothing
    and returns an empty series, which reads as a quiet window. One GET says
    which of the two it was."""
    fake = _known(results=[])
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="span_token_usage",
            breakdown="model",
            series="banana_tokens",
            client=fake,
        )
    message = str(exc.value)
    assert "'banana_tokens' is not a usage key in this project" in message
    assert "total_tokens, prompt_tokens, completion_tokens" in message


@pytest.mark.anyio
async def test_a_default_the_project_does_not_record_says_it_was_the_default() -> None:
    """The caller did not type total_tokens, so being told their argument is
    wrong would be a lie."""
    fake = _known(results=[])
    fake.usage_names = ["original_usage.total_tokens"]
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="span_token_usage",
            breakdown="model",
            client=fake,
        )
    message = str(exc.value)
    assert "defaults to series='total_tokens'" in message
    assert "original_usage.total_tokens" in message


@pytest.mark.anyio
async def test_an_unrecorded_score_name_lists_the_ones_that_exist() -> None:
    fake = _known(results=[])
    with pytest.raises(ToolError) as exc:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="span_feedback_scores",
            breakdown="model",
            series="Halluc",
            client=fake,
        )
    assert "Hallucination, Answer Relevance" in str(exc.value)


@pytest.mark.anyio
async def test_a_recorded_name_with_no_data_says_the_window_is_empty() -> None:
    """Otherwise the agent doubts the name and retries with another one."""
    fake = _known(results=[])
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_token_usage",
        breakdown="model",
        series="prompt_tokens",
        client=fake,
    )
    assert "series='prompt_tokens' is a usage key this project records" in out
    assert "the window is empty" in out


@pytest.mark.anyio
async def test_an_answer_with_data_is_not_charged_a_name_lookup() -> None:
    """A chart with numbers has proved its own name."""
    fake = _known(results=[_series("gpt-4o", [12.0, 8.0])])
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_token_usage",
        breakdown="model",
        series="prompt_tokens",
        client=fake,
    )
    assert fake.name_lookups == 0
    assert "2026-09-02 | 12" in out


@pytest.mark.anyio
async def test_a_name_lookup_that_fails_does_not_refuse_the_name() -> None:
    """Refusing a name we could not check would turn a slow endpoint into a
    wrong answer."""
    fake = _known(results=[])
    fake.usage_names = None  # the endpoint fails
    out = await run_list(
        "project_metric",
        project_id=PROJECT,
        metric_type="span_token_usage",
        breakdown="model",
        series="prompt_tokens",
        client=fake,
    )
    assert "No span_token_usage data in this window." in out
    assert "not a usage key" not in out
