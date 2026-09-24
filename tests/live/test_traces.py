"""Traces and spans, read and listed against the seeded backend."""

from __future__ import annotations

import pytest
from scripts.seed_e2e_backend import Manifest

from tests.live.conftest import Live

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_a_window_returns_exactly_the_traces_logged_in_it(
    mcp: Live, manifest: Manifest
) -> None:
    m = manifest
    recent = await mcp.list(
        "trace", project_name=m.project_name, since=m.recent_since, until=m.anchor, size=1
    )
    previous = await mcp.list(
        "trace", project_name=m.project_name, since=m.previous_since, until=m.recent_since, size=1
    )
    assert (recent.total(), previous.total()) == (m.recent_sdk_traces, m.previous_sdk_traces)


async def test_an_error_filter_returns_exactly_the_errored_traces(
    mcp: Live, manifest: Manifest
) -> None:
    m = manifest
    answer = await mcp.list(
        "trace",
        project_name=m.project_name,
        filters="error_info is_not_empty",
        since=m.recent_since,
        until=m.anchor,
        size=100,
    )
    assert set(answer.column("id")) == set(m.recent_error_trace_ids)


async def test_a_score_filter_returns_exactly_the_traces_below_it(
    mcp: Live, manifest: Manifest
) -> None:
    answer = await mcp.list(
        "trace",
        project_name=manifest.project_name,
        filters="feedback_scores.correctness < 0.5",
        size=100,
    )
    assert set(answer.column("id")) == set(manifest.low_correctness_trace_ids)


async def test_sorting_by_duration_puts_the_slowest_trace_first(
    mcp: Live, manifest: Manifest
) -> None:
    answer = await mcp.list(
        "trace", project_name=manifest.project_name, sort="duration desc", size=3
    )
    assert answer.column("id")[0] == manifest.heavy.id


async def test_two_pages_are_disjoint_and_together_hold_both(mcp: Live, manifest: Manifest) -> None:
    m = manifest
    window = {"project_name": m.project_name, "since": m.recent_since, "until": m.anchor}
    first = await mcp.list("trace", **window, size=100, page=1)
    second = await mcp.list("trace", **window, size=100, page=2)
    one, two = set(first.column("id")), set(second.column("id"))
    assert (len(one), len(two), one & two) == (100, 100, set())


async def test_fields_returns_the_named_columns_and_the_id(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list(
        "trace", project_name=manifest.project_name, fields=["name", "source"], size=5
    )
    assert set(answer.rows()[0]) == {"id", "name", "source"}


async def test_free_text_search_finds_a_trace_by_its_output(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list("trace", project_name=manifest.project_name, search="pong", size=10)
    assert answer.column("id") == [manifest.tiny.id]


async def test_a_trace_read_inlines_every_span_it_has(mcp: Live, manifest: Manifest) -> None:
    record = (await mcp.read("trace", manifest.typical.id)).record()
    spans = record["spans"]
    assert isinstance(spans, list)
    assert len(spans) == manifest.typical.span_count


async def test_a_trace_with_no_spans_reads_as_one(mcp: Live, manifest: Manifest) -> None:
    record = (await mcp.read("trace", manifest.tiny.id)).record()
    trace = record["trace"]
    assert isinstance(trace, dict)
    assert (trace["id"], record["spans"]) == (manifest.tiny.id, [])


async def test_a_trace_read_with_fields_keeps_only_those_and_the_id(
    mcp: Live, manifest: Manifest
) -> None:
    record = (await mcp.read("trace", manifest.typical.id, fields=["trace.name"])).record()
    assert record == {"trace": {"id": manifest.typical.id, "name": manifest.typical.name}}


async def test_a_traces_spans_are_listed_and_each_reads_whole(
    mcp: Live, manifest: Manifest
) -> None:
    listed = await mcp.list(
        "span",
        project_name=manifest.project_name,
        filters=f'trace_id = "{manifest.typical.id}"',
        size=50,
    )
    ids = listed.column("id")
    assert len(ids) == manifest.typical.span_count
    span = (await mcp.read("span", ids[0])).record()
    body = span.get("span", span)
    assert isinstance(body, dict)
    assert body["trace_id"] == manifest.typical.id


async def test_a_span_filter_on_type_selects_only_that_type(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list(
        "span",
        project_name=manifest.project_name,
        filters=f'trace_id = "{manifest.typical.id}" AND type = "llm"',
        fields=["type"],
        size=50,
    )
    assert answer.column("type") == ["llm"]
