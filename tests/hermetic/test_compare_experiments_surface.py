"""Comparing two experiments case by case, end to end over stdio.

WHAT THIS COVERS THAT NOTHING ELSE DOES. The in-process suites drive
``run_list`` against a fake client: they prove the wording and the refusals,
and they cannot see whether the request we send is the one opik-backend
answers. The joined endpoint is picky — the experiment ids ride in a query
param as one JSON array, a filter on the runs comes back with the
other runs stripped off the row — so the request shape is most of what can
break here.

The first section drives the stub over plain HTTP. It is the stub's own
contract: everything below it, and the scaling scenario in particular, is
only as trustworthy as the claim that a page of a 100,000-case suite costs
what a page of a 20-case suite costs.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.hermetic.stub_backend import (
    EXPERIMENT_A,
    EXPERIMENT_B,
    EXPERIMENT_OTHER_SUITE,
    OTHER_SUITE_ID,
    SUITE_ID,
    CompareSuite,
    ExperimentSpec,
    StubBackend,
)

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


def _get(stub: StubBackend, path: str, **params: Any) -> dict[str, Any]:
    """One GET against the stub, as opik-backend would be called."""
    response = httpx.get(f"http://127.0.0.1:{stub.port}/api{path}", params=params, timeout=30)
    response.raise_for_status()
    body = response.json()
    assert isinstance(body, dict)
    return body


_JOINED = f"/v1/private/datasets/{SUITE_ID}/items/experiments/items"
#: The ids as the backend reads them: one JSON array, not comma-joined.
_BOTH = json.dumps([EXPERIMENT_A, EXPERIMENT_B], separators=(",", ":"))


# --- the stub's own contract ----------------------------------------------- #


@pytest.mark.hermetic
def test_the_joined_route_slices_the_suite_by_page_and_size(backend: StubBackend) -> None:
    backend.suite = CompareSuite(case_count=100)

    body = _get(backend, _JOINED, experiment_ids=_BOTH, page=2, size=25)

    assert body["total"] == 100
    ids = [row["id"] for row in body["content"]]
    assert len(ids) == 25
    # Rows 26-50 of the suite, and no others: the window is the page asked for.
    assert ids[0].endswith("100000250000")
    assert ids[-1].endswith("100000490000")


@pytest.mark.hermetic
def test_a_hundred_thousand_case_suite_builds_only_the_page_asked_for(
    backend: StubBackend,
) -> None:
    """The scaling scenario rests on this: the suite is described, not stored."""
    backend.suite = CompareSuite(case_count=100_000)

    body = _get(backend, _JOINED, experiment_ids=_BOTH, page=1, size=5)

    assert body["total"] == 100_000
    assert [row["id"][-12:] for row in body["content"]] == [
        f"1{index:07d}0000" for index in range(5)
    ]


@pytest.mark.hermetic
def test_an_experiment_carries_its_suite_and_how_it_was_evaluated(backend: StubBackend) -> None:
    backend.experiments[EXPERIMENT_A] = ExperimentSpec(name="rerank-v1")
    backend.experiments["plain"] = ExperimentSpec(name="evaluate-run", evaluation_method="dataset")

    suite_run = _get(backend, f"/v1/private/experiments/{EXPERIMENT_A}")
    other = _get(backend, f"/v1/private/experiments/{EXPERIMENT_OTHER_SUITE}")
    plain = _get(backend, "/v1/private/experiments/plain")

    assert (suite_run["dataset_id"], suite_run["name"]) == (SUITE_ID, "rerank-v1")
    assert suite_run["evaluation_method"] == "evaluation_suite"
    assert other["dataset_id"] == OTHER_SUITE_ID
    assert plain["evaluation_method"] == "dataset"


@pytest.mark.hermetic
def test_a_row_carries_every_runs_scores_assertions_and_run_summary(backend: StubBackend) -> None:
    backend.experiments[EXPERIMENT_B] = ExperimentSpec(
        name="rerank-v3", fails_every=4, runs_per_item=2
    )

    body = _get(backend, _JOINED, experiment_ids=_BOTH, page=1, size=4)
    regressed = body["content"][3]

    runs = [item for item in regressed["experiment_items"] if item["experiment_id"] == EXPERIMENT_B]
    assert [run["status"] for run in runs] == ["passed", "failed"]
    assert runs[1]["assertion_results"][0]["passed"] is False
    assert "names Lyon" in runs[1]["assertion_results"][0]["reason"]
    assert regressed["run_summaries_by_experiment"][EXPERIMENT_B] == {
        "passed_runs": 1,
        "total_runs": 2,
        "status": "failed",
    }
    assert regressed["run_summaries_by_experiment"][EXPERIMENT_A]["status"] == "passed"


@pytest.mark.hermetic
def test_the_output_columns_route_names_the_runs_output_keys(backend: StubBackend) -> None:
    body = _get(backend, f"{_JOINED}/output/columns", experiment_ids=_BOTH)

    assert [column["name"] for column in body["columns"]] == ["input", "answer", "reasoning"]


@pytest.mark.hermetic
@pytest.mark.parametrize("route", ["", "/output/columns"])
def test_comma_joined_ids_are_a_400_the_way_the_backend_answers_them(
    backend: StubBackend, route: str
) -> None:
    """The bug this stub could not see once.

    ``ParamsValidator.getIds`` deserializes the param as JSON, so the
    comma-separated list every other multi-value param takes is a 400 here.
    The stub used to accept it, the suite went green, and the call failed
    against the real backend with ``Invalid query param ids``.
    """
    response = httpx.get(
        f"http://127.0.0.1:{backend.port}/api{_JOINED}{route}",
        params={"experiment_ids": f"{EXPERIMENT_A},{EXPERIMENT_B}"},
        timeout=30,
    )

    assert response.status_code == 400
    assert "Invalid query param ids" in response.json()["message"]


@pytest.mark.hermetic
def test_an_unknown_route_is_still_a_404(backend: StubBackend) -> None:
    response = httpx.get(f"http://127.0.0.1:{backend.port}/api/v1/private/nope", timeout=30)

    assert response.status_code == 404


# --- the comparison, over stdio -------------------------------------------- #


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_comparing_two_experiments_lines_their_cases_up(backend: StubBackend) -> None:
    backend.suite = CompareSuite(case_count=8)

    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            size=4,
        )

    # The suite was resolved from the experiments, not asked for.
    # The page and the output keys go out together, so their order is a race;
    # what matters is that the suite was resolved from the experiments first.
    # Beside the page: the score definitions once, and the figures once per
    # experiment — never once per row.
    assert sorted(request.path for request in backend.requests) == sorted(
        [
            f"/v1/private/experiments/{EXPERIMENT_A}",
            f"/v1/private/experiments/{EXPERIMENT_B}",
            "/v1/private/feedback-definitions",
            _JOINED,
            f"{_JOINED}/stats",
            f"{_JOINED}/stats",
            f"{_JOINED}/output/columns",
        ]
    )
    joined = next(r for r in backend.sent(_JOINED) if r.path.endswith("items"))
    assert joined.query["experiment_ids"] == [_BOTH]
    assert joined.query["truncate"] == ["true"]
    stats = backend.sent(f"{_JOINED}/stats")
    assert sorted(r.query["experiment_ids"][0] for r in stats) == sorted(
        [json.dumps([EXPERIMENT_A]), json.dumps([EXPERIMENT_B])]
    )

    assert answer.startswith(
        "[list: dataset_item | compare: "
        f"E1 = baseline rerank-v1 ({EXPERIMENT_A}), E2 = rerank-v3 ({EXPERIMENT_B})]"
    )
    assert "Found 8 dataset_items (page 1, showing 4 of 8):" in answer
    # The figures are over the whole suite, not the page: B fails every fourth
    # of eight cases, so its mean is (6 * 0.9 + 2 * 0.4) / 8.
    assert "E1: 8 runs; correctness 0.9, hallucination 1; avg cost 0.0001; p50 1204 ms" in answer
    assert "E2: 8 runs; correctness 0.775, hallucination 1; avg cost 0.0001; p50 1204 ms" in answer
    assert "E1 is the baseline" in answer
    assert "Δ is E2 minus E1" in answer
    # The fourth case is the one rerank-v3 regressed on.
    regressed = [line for line in answer.splitlines() if line.startswith("0199c6a4")][3]
    assert "0.9 / 0.4 Δ-0.5" in regressed
    assert "1/1·0/1" in regressed
    assert "(E2)" in regressed and "names Lyon, not Paris." in regressed
    assert "Use page=2 for next 4 results." in answer
    # ``input`` is the suite's own case, echoed back by the run.
    assert "runs' output keys: answer, reasoning" in answer
    assert "case data keys: expected_answer, question" in answer


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_a_run_that_produced_nothing_reads_errored_end_to_end(
    backend: StubBackend,
) -> None:
    """The case the in-process suite can only assert about a dict: what the
    joined endpoint really returns for a run whose task or judge raised is an
    item with no scores, no assertions and no status — no error field, because
    the payload has none. The table has to read that as errored rather than as
    a zero, and count it apart from the cases that were scored."""
    backend.suite = CompareSuite(case_count=4)
    backend.experiments[EXPERIMENT_B] = ExperimentSpec(name="rerank-v3", errors_every=4)

    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            size=4,
        )

    errored = [line for line in answer.splitlines() if line.startswith("0199c6a4")][3]
    # id | data.expected_answer | data.question | correctness | hallucination | …
    assert errored.split(" | ")[3] == "0.9 / errored", errored
    assert errored.split(" | ")[4] == "1 / errored", "every score column, not just the first"
    assert "Δ" not in errored, "an error is not a difference"
    assert "3 of 4 cases fully scored, 1 errored" in answer
    assert "and 0 unscored" in answer
    assert "open its worst_trace for error_info" in answer


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_naming_the_fields_narrows_the_comparison_to_one_question_and_one_score(
    backend: StubBackend,
) -> None:
    """OPIK-8399's own acceptance criterion, over the wire.

    What only this test can see is the argument crossing the MCP boundary: an
    array of strings through FastMCP's schema and Pydantic's validation, which
    the in-process suites call ``run_list`` underneath. The narrowing itself is
    the point — N experiments times M keys becomes one question and one score.
    """
    backend.suite = CompareSuite(case_count=8)

    async with _session(backend) as session:
        wide = await _call(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            size=4,
        )
        narrow = await _call(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            size=4,
            fields=["data.question", "feedback_scores.correctness"],
        )
        refusal = await _refuse(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            fields=["data.quesiton"],
        )

    columns = next(line for line in narrow.splitlines() if line.startswith("id |"))
    # The score column keeps the direction marking OPIK-8394 put on it: a
    # projection narrows what is shown, never what a shown cell means.
    assert columns == "id | data.question | correctness (direction unknown) | worst_trace"
    # The rows are those four cells. The case key the caller did not name and
    # the score they did not ask for are gone from every one of them, and the
    # trace that opens the case is on every one of them anyway.
    rows = [line for line in narrow.splitlines() if line.startswith("0199c6a4")]
    assert len(rows) == 4
    assert all(line.count(" | ") == 3 and "(E" in line for line in rows)
    assert len(narrow) < len(wide)
    # The per-experiment figures above the table are not row fields and are
    # left alone: they say how many runs each mean is over, which is what
    # makes a narrowed table safe to read rather than noise on top of it.
    assert "E1: 8 runs; correctness 0.9, hallucination 1" in narrow

    # Spec D3: it says it was projected, and what it left out.
    marker = next(line for line in narrow.splitlines() if line.startswith("projected:"))
    assert "omitted:" in marker
    assert "feedback_scores.hallucination" in marker

    # An unknown field is an error naming the valid ones, never a blank column.
    assert "data.quesiton" in refusal
    assert "data.question" in refusal
    assert "feedback_scores.correctness" in refusal


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_a_comparison_costs_the_same_on_twenty_and_on_a_hundred_thousand_cases(
    backend: StubBackend,
) -> None:
    """The whole point of the joined endpoint: the page is the cost, not the suite.

    The same diagnosis on a suite five thousand times larger must take the
    same number of calls and about the same number of tokens, or the agent is
    back to reading every trace.
    """
    answers: dict[int, str] = {}
    calls: dict[int, list[str]] = {}
    for case_count in (20, 100_000):
        backend.suite = CompareSuite(case_count=case_count)
        backend.requests.clear()
        async with _session(backend) as session:
            answers[case_count] = await _call(
                session,
                "list",
                entity_type="dataset_item",
                experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
                size=5,
            )
        calls[case_count] = [request.path for request in backend.requests]

    # The experiment reads go out together, so their order in the stub's log is
    # a race; the claim is the same requests, not the same sequence.
    assert sorted(calls[20]) == sorted(calls[100_000])
    ratio = len(answers[100_000]) / len(answers[20])
    assert 1 / 1.2 <= ratio <= 1.2, f"answer grew {ratio:.2f}x with the suite"


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_experiments_from_two_datasets_are_refused_before_anything_is_joined(
    backend: StubBackend,
) -> None:
    async with _session(backend) as session:
        refusal = await _refuse(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_OTHER_SUITE],
        )

    assert "support-qa" in refusal and "billing-qa" in refusal
    assert not backend.called("items/experiments/items")


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_a_filter_on_the_runs_comes_back_with_every_run_on_the_row(
    backend: StubBackend,
) -> None:
    """The backend answers a run-level filter with the other runs stripped off.
    Reading that page as it arrives cannot tell a regression from a case that
    was always bad, so each matched case is fetched again without the clause."""
    backend.suite = CompareSuite(case_count=8, strip_to_experiment=EXPERIMENT_B)

    async with _session(backend) as session:
        answer = await _call(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            filters="feedback_scores.correctness < 0.5",
            size=4,
        )

    joined = [r for r in backend.sent(_JOINED) if r.path.endswith("items")]
    assert len(joined) == 5, "one filtered page, then one refetch per row on it"
    sent = '[{"field":"feedback_scores","operator":"<","key":"correctness","value":"0.5"}]'
    assert joined[0].query["filters"] == [sent]
    for request in joined[1:]:
        assert '"field":"id"' in request.query["filters"][0]
        assert request.query["size"] == ["1"]

    rows = [line for line in answer.splitlines() if line.startswith("0199c6a4")]
    assert all(" / " in row for row in rows), "both runs are on every row again"
    assert "0.9 / 0.4 Δ-0.5" in rows[3]
    assert "matches a case when any of its experiments matches" in answer

    # The figures take the same filter, so "how many scored under 0.5 in each"
    # is read off the header: none of A's eight runs, two of B's.
    stats = backend.sent(f"{_JOINED}/stats")
    assert [r.query["filters"] for r in stats] == [[sent], [sent]]
    assert "E1: 0 runs match" in answer
    assert "E2: 2 runs; correctness 0.4, hallucination 1;" in answer
    assert "figures over the runs matching the filter" in answer


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_a_filtered_comparison_costs_the_same_on_a_hundred_thousand_cases(
    backend: StubBackend,
) -> None:
    answers: dict[int, str] = {}
    calls: dict[int, list[str]] = {}
    for case_count in (20, 100_000):
        backend.suite = CompareSuite(case_count=case_count, strip_to_experiment=EXPERIMENT_B)
        backend.requests.clear()
        async with _session(backend) as session:
            answers[case_count] = await _call(
                session,
                "list",
                entity_type="dataset_item",
                experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
                filters="feedback_scores.correctness < 0.5",
                size=5,
            )
        calls[case_count] = [request.path for request in backend.requests]

    # The experiment reads go out together, so their order in the stub's log is
    # a race; the claim is the same requests, not the same sequence.
    assert sorted(calls[20]) == sorted(calls[100_000])
    ratio = len(answers[100_000]) / len(answers[20])
    assert 1 / 1.2 <= ratio <= 1.2, f"answer grew {ratio:.2f}x with the suite"


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_reading_an_experiment_names_the_call_that_compares_it(
    backend: StubBackend,
) -> None:
    async with _session(backend) as session:
        answer = await _call(session, "read", entity_type="experiment", id=EXPERIMENT_A)

    assert "list('dataset_item', experiment_ids=" in answer
    assert EXPERIMENT_A in answer
