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

from tests.e2e.stub_backend import (
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


@pytest.mark.e2e
def test_the_joined_route_slices_the_suite_by_page_and_size(backend: StubBackend) -> None:
    backend.suite = CompareSuite(case_count=100)

    body = _get(backend, _JOINED, experiment_ids=_BOTH, page=2, size=25)

    assert body["total"] == 100
    ids = [row["id"] for row in body["content"]]
    assert len(ids) == 25
    # Rows 26-50 of the suite, and no others: the window is the page asked for.
    assert ids[0].endswith("100000250000")
    assert ids[-1].endswith("100000490000")


@pytest.mark.e2e
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


@pytest.mark.e2e
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


@pytest.mark.e2e
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


@pytest.mark.e2e
def test_the_output_columns_route_names_the_runs_output_keys(backend: StubBackend) -> None:
    body = _get(backend, f"{_JOINED}/output/columns", experiment_ids=_BOTH)

    assert [column["name"] for column in body["columns"]] == ["input", "answer", "reasoning"]


@pytest.mark.e2e
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


@pytest.mark.e2e
def test_an_unknown_route_is_still_a_404(backend: StubBackend) -> None:
    response = httpx.get(f"http://127.0.0.1:{backend.port}/api/v1/private/nope", timeout=30)

    assert response.status_code == 404


# --- the comparison, over stdio -------------------------------------------- #


@pytest.mark.e2e
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
    assert sorted(request.path for request in backend.requests) == sorted(
        [
            f"/v1/private/experiments/{EXPERIMENT_A}",
            f"/v1/private/experiments/{EXPERIMENT_B}",
            _JOINED,
            f"{_JOINED}/output/columns",
        ]
    )
    joined = next(r for r in backend.sent(_JOINED) if not r.path.endswith("columns"))
    assert joined.query["experiment_ids"] == [_BOTH]
    assert joined.query["truncate"] == ["true"]

    assert answer.startswith(
        "[list: dataset_item | compare: "
        f"E1 = baseline rerank-v1 ({EXPERIMENT_A}), E2 = rerank-v3 ({EXPERIMENT_B})]"
    )
    assert "Found 8 dataset_items (page 1, showing 4 of 8):" in answer
    assert "E1 is the baseline" in answer
    # The fourth case is the one rerank-v3 regressed on.
    regressed = [line for line in answer.splitlines() if line.startswith("0199c6a4")][3]
    assert "0.9 / 0.4 Δ0.5" in regressed
    assert "1/1·0/1" in regressed
    assert "(E2)" in regressed and "names Lyon, not Paris." in regressed
    assert "Use page=2 for next 4 results." in answer
    # ``input`` is the suite's own case, echoed back by the run.
    assert "runs' output keys: answer, reasoning" in answer
    assert "case data keys: expected_answer, question" in answer


@pytest.mark.e2e
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

    assert calls[20] == calls[100_000]
    ratio = len(answers[100_000]) / len(answers[20])
    assert 1 / 1.2 <= ratio <= 1.2, f"answer grew {ratio:.2f}x with the suite"


@pytest.mark.e2e
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


@pytest.mark.e2e
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

    joined = [r for r in backend.sent(_JOINED) if not r.path.endswith("columns")]
    assert len(joined) == 5, "one filtered page, then one refetch per row on it"
    assert joined[0].query["filters"] == [
        '[{"field":"feedback_scores","operator":"<","key":"correctness","value":"0.5"}]'
    ]
    for request in joined[1:]:
        assert '"field":"id"' in request.query["filters"][0]
        assert request.query["size"] == ["1"]

    rows = [line for line in answer.splitlines() if line.startswith("0199c6a4")]
    assert all(" / " in row for row in rows), "both runs are on every row again"
    assert "0.9 / 0.4 Δ0.5" in rows[3]
    assert "matches a case when any of its experiments matches" in answer


@pytest.mark.e2e
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

    assert calls[20] == calls[100_000]
    ratio = len(answers[100_000]) / len(answers[20])
    assert 1 / 1.2 <= ratio <= 1.2, f"answer grew {ratio:.2f}x with the suite"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_reading_an_experiment_names_the_call_that_compares_it(
    backend: StubBackend,
) -> None:
    async with _session(backend) as session:
        answer = await _call(session, "read", entity_type="experiment", id=EXPERIMENT_A)

    assert "list('dataset_item', experiment_ids=" in answer
    assert EXPERIMENT_A in answer
