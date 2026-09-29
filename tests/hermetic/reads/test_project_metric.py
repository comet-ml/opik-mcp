"""list('project_metric'): a metric series as a table, with the quirks of the endpoint."""

from __future__ import annotations

import json

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    call,
    refuse,
    retired_addresses,
    text,
)
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_ID

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("list", {"project_id": PROJECT_ID, "metric_type": "trace_count"})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "project_metric", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "project_metric", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    args: dict[str, object] = {
        "entity_type": "project_metric",
        "project_id": PROJECT_ID,
        "metric_type": "trace_count",
    }
    async with http.session() as session:
        answers = [await text(session, "list", args)]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_a_metric_series_renders_as_a_table_and_echoes_what_applied(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_count",
            interval="daily",
            since="2026-09-01T00:00:00Z",
            until="2026-09-03T00:00:00Z",
        )

    header, *rows = answer.splitlines()
    assert " tok | trace_count | daily |" in header
    assert 'filters: source = "sdk"' in header
    assert rows[0] == "time | traces"
    assert rows[1:3] == ["2026-09-01 | 0", "2026-09-02 | 4"]

    sent = backend.one("/metrics")
    assert sent.payload["metric_type"] == "TRACE_COUNT"
    assert sent.payload["interval"] == "DAILY"
    assert sent.payload["trace_filters"] == [
        {"field": "source", "operator": "=", "key": "", "value": "sdk"}
    ]


async def test_a_grouped_series_is_keyed_by_time_and_says_what_others_is(
    backend: StubBackend,
) -> None:
    """The grouped queries are not filled, so groups arrive different lengths
    and ``__others__`` arrives unaggregated. Read by position, one group's
    only bucket lands under another group's date."""
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="span_count",
            breakdown="model",
            interval="daily",
            since="2026-09-01T00:00:00Z",
            until="2026-09-04T00:00:00Z",
        )

    rows = answer.splitlines()[1:]
    assert rows[0] == "time | gpt-4o | claude-opus-4 | __others__"
    assert rows[1] == "2026-09-01 | 10 |  | ", "only gpt-4o ran that day"
    assert rows[2] == "2026-09-02 |  | 4 | 5", "the day gpt-4o has no bucket for at all"
    assert rows[3] == "2026-09-03 | 12 |  | "
    assert "__others__ is the backend's own bucket" in answer

    assert backend.one("/metrics").payload["breakdown"] == {"field": "MODEL"}


async def test_grouping_a_token_metric_sends_the_sub_metric_the_backend_needs(
    backend: StubBackend,
) -> None:
    """Without it the backend answers 422, and there was no argument to
    comply with until ``series`` existed."""
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="span_token_usage",
            breakdown="model",
        )

    assert "by model (total_tokens)" in answer.splitlines()[0]
    assert backend.one("/metrics").payload["breakdown"] == {
        "field": "MODEL",
        "sub_metric": "total_tokens",
    }


async def test_a_named_series_travels_verbatim(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        await call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_feedback_scores",
            breakdown="tags",
            series="Answer Relevance",
        )

    assert backend.one("/metrics").payload["breakdown"]["sub_metric"] == "Answer Relevance"


async def test_a_rate_is_charted_against_the_count_of_what_it_measures(
    backend: StubBackend,
) -> None:
    """0% error over a day with no traces is not a measurement. The count
    rides along so the empty buckets can be left out and counted."""
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_error_rate",
            interval="daily",
            since="2026-09-01T00:00:00Z",
            until="2026-09-03T00:00:00Z",
        )

    # What the rate is, weighted by the count, is the backend's arithmetic, and
    # tests/live/test_project.py checks it against real traces. This checks the
    # shape: the empty bucket is left out and said to be.
    rows = answer.splitlines()[1:]
    # The window's first day is named in the header; what must not appear is a
    # row for it, since there were no traces to measure a rate over.
    assert not any(row.startswith("2026-09-01") for row in rows)
    assert "1 of 2 buckets are not listed: no traces in them" in answer

    # Sorted, not in arrival order: the two go out concurrently on one
    # connection, which is the point of pairing them, so which lands first is
    # the event loop's business. Pinning the order here made this test fail
    # about one run in three.
    kinds = sorted(r.payload["metric_type"] for r in backend.sent("/metrics"))
    assert kinds == ["TRACE_COUNT", "TRACE_ERROR_RATE"], "the companion count went with it"


async def test_null_buckets_are_left_out_and_counted(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_duration",
            interval="daily",
            since="2026-09-01T00:00:00Z",
            until="2026-09-03T00:00:00Z",
        )

    rows = answer.splitlines()[1:]
    assert rows[0] == "time | duration.p50 | duration.p99"
    assert rows[1] == "2026-09-02 | 120 | 980"
    assert "1 of 2 buckets are not listed: no trace_duration recorded in them" in answer


async def test_the_refusals_never_reach_the_backend(backend: StubBackend) -> None:
    """Each of these is decidable from the catalog, and each names what to do
    instead. A round trip for any of them is a round trip the user pays for."""
    async with stdio_session(backend) as session:
        cost = await refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_cost",
            breakdown="model",
        )
        ungroupable = await refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="span_error_rate",
            breakdown="model",
        )
        thread_filter = await refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="thread_count",
            filters='source = "sdk"',
        )
        paged = await refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_count",
            page=2,
        )
        no_scope = await refuse(
            session, "list", entity_type="project_metric", metric_type="trace_count"
        )

    assert "No cost metric can be grouped by model" in cost
    assert "span_count" in cost, "and where the field is accepted"
    assert "cannot be grouped at all" in ungroupable
    assert "cannot be filtered by source" in thread_filter
    assert "does not take page" in paged
    assert "requires project_id or project_name" in no_scope

    assert not backend.called("/metrics"), "not one of them was worth a call"


async def test_an_unrecorded_series_is_named_against_the_project(backend: StubBackend) -> None:
    """The one refusal that is worth a call, and only when the answer came
    back empty: the backend charts an unknown usage key as nothing, which
    reads as a quiet window."""
    backend.usage_keys = ["total_tokens"]
    async with stdio_session(backend) as session:
        refusal = await refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="span_token_usage",
            breakdown="model",
            series="banana_tokens",
        )

    assert "'banana_tokens' is not a usage key in this project" in refusal
    assert "total_tokens" in refusal
    assert backend.called("token-usage/names"), "the names were checked, not guessed"


async def test_the_schema_reference_answers_without_touching_the_backend(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        reference = json.loads(await call(session, "schema", operation="list.project_metric"))

    assert set(reference["metric_types"]) >= {"trace_count", "span_token_usage"}
    assert reference["breakdowns"]["by_metric"]["span_cost"] is None
    assert reference["multi_series"]["which"]["duration"].startswith("one series per percentile")
    assert not backend.requests, "a reference is a lookup, not a query"


async def test_a_wide_hourly_request_reaches_the_backend(backend: StubBackend) -> None:
    """The one refusal that was ours alone is gone. An hourly month is 721
    rows, and whether that is worth the context is the caller's decision; the
    server's job is to send exactly what was asked and say what it sent."""
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_count",
            interval="hourly",
            since="30d",
        )

    assert backend.one("/metrics").payload["interval"] == "HOURLY"
    assert "| hourly |" in answer.splitlines()[0]
    assert "from the window" not in answer, (
        "the caller chose it; the header does not claim otherwise"
    )
