"""The project overview and the metric series, end to end over stdio.

WHAT THIS COVERS THAT NOTHING ELSE DOES. The in-process suites drive
``run_read`` and ``run_list`` against a fake client object: they prove the
logic and the wording, and they cannot see the wiring. After OPIK-8284 split
``read_list`` into a namespace per entity, the wiring is most of what could
break — a handler that stops being registered, an import that resolves to the
wrong module, a request body that no longer matches what the endpoint takes.
None of that fails a unit test whose fake accepts any keyword.

So this runs the real server as a subprocess, over a real pipe, against a
stub backend on a real socket (``stub_backend``), and checks both halves of
each feature: the answer the agent reads, and the request the backend saw.

Still hermetic — the stub is in-process and needs no credentials — so it runs
on every PR with the rest of the e2e job.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.e2e.stub_backend import PROJECT_ID, PROJECT_NAME, TRACE_ID, StubBackend

_TIMEOUT_S = 60


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def backend() -> Iterator[StubBackend]:
    stub = StubBackend()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@asynccontextmanager
async def _session(stub: StubBackend) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            "OPIK_URL": f"http://127.0.0.1:{stub.port}/api",
            "OPIK_API_KEY": "stub-key",
            "OPIK_WORKSPACE": "stub-workspace",
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "OPIK_MCP_SENTRY_ENABLED": "false",
            "OPIK_MCP_LOG_LEVEL": "WARNING",
        },
    )
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


async def _call(session: ClientSession, tool: str, **args: object) -> str:
    result = await session.call_tool(tool, args)
    text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
    assert not result.isError, f"{tool}({args}) was refused: {text}"
    return text


async def _refuse(session: ClientSession, tool: str, **args: object) -> str:
    result = await session.call_tool(tool, args)
    text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
    assert result.isError, f"{tool}({args}) was answered, expected a refusal: {text}"
    return text


# --- read('project') ------------------------------------------------------- #


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_project_read_is_assembled_from_every_part(backend: StubBackend) -> None:
    """One call, five endpoints, one payload — and each part in it."""
    async with _session(backend) as session:
        answer = await _call(session, "read", entity_type="project", id=PROJECT_ID)

    payload = json.loads(answer.split("\n", 1)[1])
    assert payload["project"]["name"] == PROJECT_NAME
    assert payload["summary"]["source"] == "sdk"
    assert payload["summary"]["traces"]["count"] == {"current": 120.0, "previous": 80.0}
    assert payload["vocabulary"]["score_names"]["names"] == [
        "Hallucination",
        "Answer Relevance",
    ]
    assert payload["vocabulary"]["usage_keys"]["total"] == 3
    assert payload["vocabulary"]["online_rules"]["names"] == ["hallucination-judge"]
    assert payload["contains"]["experiment"]["name"] == "rerank-v3"
    assert payload["url"].endswith(f"/projects/{PROJECT_ID}/logs")

    # The fan-out went out as five calls on one connection, not one call that
    # the read then guessed the rest from.
    for endpoint in (
        f"/projects/{PROJECT_ID}",
        "kpi-cards",
        "feedback-scores/names",
        "token-usage/names",
        "activities",
        "automations/evaluators/",
    ):
        assert backend.called(endpoint), f"{endpoint} was never called"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_summary_asks_for_sdk_traffic_as_a_json_string(backend: StubBackend) -> None:
    """``kpi-cards`` declares ``filters`` as a String where every other
    endpoint takes an array. Sending the array shape silently drops the
    filter, and the numbers stop matching the Logs page."""
    async with _session(backend) as session:
        await _call(session, "read", entity_type="project", id=PROJECT_ID)

    sent = backend.one("kpi-cards")
    assert isinstance(sent.body and sent.payload["filters"], str)
    assert sent.filters() == [{"field": "source", "operator": "=", "key": "", "value": "sdk"}]
    assert sent.payload["entity_type"] == "traces"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_window_reaches_the_backend_and_comes_back_stated(backend: StubBackend) -> None:
    async with _session(backend) as session:
        answer = await _call(
            session,
            "read",
            entity_type="project",
            id=PROJECT_ID,
            since="2026-08-01T00:00:00Z",
            until="2026-08-31T00:00:00Z",
        )

    window = json.loads(answer.split("\n", 1)[1])["summary"]["window"]
    assert window["since"] == "2026-08-01T00:00:00Z"
    assert window["until"] == "2026-08-31T00:00:00Z"
    assert window["days"] == 30
    assert window["compared_to"] == {
        "since": "2026-07-02T00:00:00Z",
        "until": "2026-08-01T00:00:00Z",
    }
    sent = backend.one("kpi-cards")
    assert sent.payload["interval_start"] == "2026-08-01T00:00:00Z"
    assert sent.payload["interval_end"] == "2026-08-31T00:00:00Z"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_one_failing_part_does_not_take_the_read_down(backend: StubBackend) -> None:
    """Every decoration is optional; the record is not. A backend that fails
    on the score names must still answer the question that was asked."""
    backend.failing = {"feedback-scores/names"}
    async with _session(backend) as session:
        answer = await _call(session, "read", entity_type="project", id=PROJECT_ID)

    payload = json.loads(answer.split("\n", 1)[1])
    assert payload["project"]["name"] == PROJECT_NAME
    assert payload["summary"]["traces"]["count"]["current"] == 120.0
    assert "error" in payload["vocabulary"]["score_names"], "the failure is stated, not hidden"
    assert "names" not in payload["vocabulary"]["score_names"]
    assert payload["vocabulary"]["usage_keys"]["names"], "its siblings still answered"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_project_is_readable_by_name(backend: StubBackend) -> None:
    async with _session(backend) as session:
        answer = await _call(session, "read", entity_type="project", id=PROJECT_NAME)

    assert json.loads(answer.split("\n", 1)[1])["project"]["id"] == PROJECT_ID


# --- list('project_metric') ------------------------------------------------ #


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_metric_series_renders_as_a_table_and_echoes_what_applied(
    backend: StubBackend,
) -> None:
    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_count",
            since="2026-09-01T00:00:00Z",
            until="2026-09-03T00:00:00Z",
        )

    header, *rows = answer.splitlines()
    assert "project_metric | trace_count | daily" in header
    assert 'filters: source = "sdk"' in header
    assert rows[0] == "time | traces"
    assert rows[1:3] == ["2026-09-01 | 0", "2026-09-02 | 4"]

    sent = backend.one("/metrics")
    assert sent.payload["metric_type"] == "TRACE_COUNT"
    assert sent.payload["interval"] == "DAILY"
    assert sent.payload["trace_filters"] == [
        {"field": "source", "operator": "=", "key": "", "value": "sdk"}
    ]


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_grouped_series_is_keyed_by_time_and_says_what_others_is(
    backend: StubBackend,
) -> None:
    """The grouped queries are not filled, so groups arrive different lengths
    and ``__others__`` arrives unaggregated. Read by position, one group's
    only bucket lands under another group's date."""
    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="span_count",
            breakdown="model",
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


@pytest.mark.e2e
@pytest.mark.anyio
async def test_grouping_a_token_metric_sends_the_sub_metric_the_backend_needs(
    backend: StubBackend,
) -> None:
    """Without it the backend answers 422, and there was no argument to
    comply with until ``series`` existed."""
    async with _session(backend) as session:
        answer = await _call(
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


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_named_series_travels_verbatim(backend: StubBackend) -> None:
    async with _session(backend) as session:
        await _call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_feedback_scores",
            breakdown="tags",
            series="Answer Relevance",
        )

    assert backend.one("/metrics").payload["breakdown"]["sub_metric"] == "Answer Relevance"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_rate_is_charted_against_the_count_of_what_it_measures(
    backend: StubBackend,
) -> None:
    """0% error over a day with no traces is not a measurement. The count
    rides along so the empty buckets can be left out and counted."""
    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_error_rate",
            since="2026-09-01T00:00:00Z",
            until="2026-09-03T00:00:00Z",
        )

    rows = answer.splitlines()[1:]
    assert rows[1] == "2026-09-02 | 25"
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


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_sub_cent_cost_survives_the_table(backend: StubBackend) -> None:
    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_cost",
            since="2026-09-01T00:00:00Z",
            until="2026-09-03T00:00:00Z",
        )

    assert "2026-09-02 | 3.2e-05" in answer, "rounding it to 0 would read as no cost"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_null_buckets_are_left_out_and_counted(backend: StubBackend) -> None:
    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_duration",
            since="2026-09-01T00:00:00Z",
            until="2026-09-03T00:00:00Z",
        )

    rows = answer.splitlines()[1:]
    assert rows[0] == "time | duration.p50 | duration.p99"
    assert rows[1] == "2026-09-02 | 120 | 980"
    assert "1 of 2 buckets are not listed: no trace_duration recorded in them" in answer


# --- the refusals that cost nothing ---------------------------------------- #


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_refusals_never_reach_the_backend(backend: StubBackend) -> None:
    """Each of these is decidable from the catalog, and each names what to do
    instead. A round trip for any of them is a round trip the user pays for."""
    async with _session(backend) as session:
        cost = await _refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_cost",
            breakdown="model",
        )
        ungroupable = await _refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="span_error_rate",
            breakdown="model",
        )
        thread_filter = await _refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="thread_count",
            filters='source = "sdk"',
        )
        too_wide = await _refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_count",
            interval="hourly",
            since="30d",
        )
        paged = await _refuse(
            session,
            "list",
            entity_type="project_metric",
            project_id=PROJECT_ID,
            metric_type="trace_count",
            page=2,
        )
        no_scope = await _refuse(
            session, "list", entity_type="project_metric", metric_type="trace_count"
        )

    assert "No cost metric can be grouped by model" in cost
    assert "span_count" in cost, "and where the field is accepted"
    assert "cannot be grouped at all" in ungroupable
    assert "cannot be filtered by source" in thread_filter
    assert "721 time buckets" in too_wide and "interval='daily'" in too_wide
    assert "does not take page" in paged
    assert "requires project_id or project_name" in no_scope

    assert not backend.called("/metrics"), "not one of them was worth a call"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_an_unrecorded_series_is_named_against_the_project(backend: StubBackend) -> None:
    """The one refusal that is worth a call, and only when the answer came
    back empty: the backend charts an unknown usage key as nothing, which
    reads as a quiet window."""
    backend.usage_keys = ["total_tokens"]
    async with _session(backend) as session:
        refusal = await _refuse(
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


# --- the two name lists ---------------------------------------------------- #


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_name_lists_answer_under_a_project(backend: StubBackend) -> None:
    async with _session(backend) as session:
        scores = await _call(session, "list", entity_type="score_name", project_id=PROJECT_ID)
        rules = await _call(session, "list", entity_type="online_rule", project_name=PROJECT_NAME)

    assert "Hallucination" in scores and "Answer Relevance" in scores
    assert "trace, span and thread scores together" in scores, "what a name does not say"
    assert "hallucination-judge" in rules
    assert "llm_as_judge" in rules and "0.5" in rules


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_score_name_page_is_cut_here_and_says_so(backend: StubBackend) -> None:
    """The endpoint has no paging of its own, so returning everything with a
    total of everything promised a page 2 that returned the same rows."""
    backend.score_names = [f"score-{i:02d}" for i in range(30)]
    async with _session(backend) as session:
        first = await _call(
            session, "list", entity_type="score_name", project_id=PROJECT_ID, size=10
        )
        second = await _call(
            session, "list", entity_type="score_name", project_id=PROJECT_ID, size=10, page=2
        )

    assert "score-00" in first and "score-09" in first and "score-10" not in first
    assert "score-10" in second and "score-00" not in second


# --- the reference, and the rest of the surface still standing ------------- #


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_schema_reference_answers_without_touching_the_backend(
    backend: StubBackend,
) -> None:
    async with _session(backend) as session:
        reference = json.loads(await _call(session, "schema", operation="list.project_metric"))

    assert set(reference["metric_types"]) >= {"trace_count", "span_token_usage"}
    assert reference["breakdowns"]["by_metric"]["span_cost"] is None
    assert reference["multi_series"]["which"]["duration"].startswith("one series per percentile")
    assert not backend.requests, "a reference is a lookup, not a query"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_entities_the_split_moved_still_answer(backend: StubBackend) -> None:
    """The refactor gave every entity its own namespace. A handler that fell
    out of the registry fails nothing until someone calls it."""
    async with _session(backend) as session:
        traces = await _call(session, "list", entity_type="trace", project_id=PROJECT_ID, size=5)
        trace = await _call(session, "read", entity_type="trace", id=TRACE_ID)
        projects = await _call(session, "list", entity_type="project")

    assert TRACE_ID in traces
    assert '"name": "checkout"' in trace
    assert PROJECT_NAME in projects
