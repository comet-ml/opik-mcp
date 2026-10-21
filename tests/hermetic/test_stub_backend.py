"""The stub's own contract, over plain HTTP, as opik-backend would be called.

Everything in ``reads/`` is only as trustworthy as these claims: that the
joined and items routes page what they are asked for, that a page of a
100,000-case suite costs what a page of a 20-case suite costs, and that the
stub answers 400 where the backend's validators do.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_compare import CompareSuite, ExperimentSpec
from tests.hermetic.stub_records import (
    CASE_ID,
    EXPERIMENT_A,
    EXPERIMENT_B,
    EXPERIMENT_OTHER_SUITE,
    OTHER_SUITE_ID,
    SUITE_ID,
)

pytestmark = pytest.mark.hermetic

_JOINED = f"/v1/private/datasets/{SUITE_ID}/items/experiments/items"
_ITEMS = f"/v1/private/datasets/{SUITE_ID}/items"
#: The ids as the backend reads them: one JSON array, not comma-joined.
_BOTH = json.dumps([EXPERIMENT_A, EXPERIMENT_B], separators=(",", ":"))


@pytest.fixture
def backend() -> Iterator[StubBackend]:
    stub = StubBackend()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


def _get(stub: StubBackend, path: str, **params: str | int) -> httpx.Response:
    return httpx.get(f"http://127.0.0.1:{stub.port}/api{path}", params=params, timeout=30)


def _ok(stub: StubBackend, path: str, **params: str | int) -> httpx.Response:
    """One GET that must succeed, as opik-backend would be called."""
    response = _get(stub, path, **params)
    response.raise_for_status()
    return response


# --- the comparison routes -------------------------------------------------- #


def test_the_joined_route_slices_the_suite_by_page_and_size(backend: StubBackend) -> None:
    backend.suite = CompareSuite(case_count=100)

    body = _ok(backend, _JOINED, experiment_ids=_BOTH, page=2, size=25).json()

    assert body["total"] == 100
    ids = [row["id"] for row in body["content"]]
    assert len(ids) == 25
    # Rows 26-50 of the suite, and no others: the window is the page asked for.
    assert ids[0].endswith("100000250000")
    assert ids[-1].endswith("100000490000")


def test_a_hundred_thousand_case_suite_builds_only_the_page_asked_for(
    backend: StubBackend,
) -> None:
    """The scaling scenario rests on this: the suite is described, not stored."""
    backend.suite = CompareSuite(case_count=100_000)

    body = _ok(backend, _JOINED, experiment_ids=_BOTH, page=1, size=5).json()

    assert body["total"] == 100_000
    assert [row["id"][-12:] for row in body["content"]] == [
        f"1{index:07d}0000" for index in range(5)
    ]


def test_an_experiment_carries_its_suite_and_how_it_was_evaluated(backend: StubBackend) -> None:
    backend.experiments[EXPERIMENT_A] = ExperimentSpec(name="rerank-v1")
    backend.experiments["plain"] = ExperimentSpec(name="evaluate-run", evaluation_method="dataset")

    suite_run = _ok(backend, f"/v1/private/experiments/{EXPERIMENT_A}").json()
    other = _ok(backend, f"/v1/private/experiments/{EXPERIMENT_OTHER_SUITE}").json()
    plain = _ok(backend, "/v1/private/experiments/plain").json()

    assert (suite_run["dataset_id"], suite_run["name"]) == (SUITE_ID, "rerank-v1")
    assert suite_run["evaluation_method"] == "evaluation_suite"
    assert other["dataset_id"] == OTHER_SUITE_ID
    assert plain["evaluation_method"] == "dataset"


def test_a_row_carries_every_runs_scores_assertions_and_run_summary(backend: StubBackend) -> None:
    backend.experiments[EXPERIMENT_B] = ExperimentSpec(
        name="rerank-v3", fails_every=4, runs_per_item=2
    )

    body = _ok(backend, _JOINED, experiment_ids=_BOTH, page=1, size=4).json()
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


def test_the_output_columns_route_names_the_runs_output_keys(backend: StubBackend) -> None:
    body = _ok(backend, f"{_JOINED}/output/columns", experiment_ids=_BOTH).json()

    assert [column["name"] for column in body["columns"]] == ["input", "answer", "reasoning"]


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


def test_an_unknown_route_is_still_a_404(backend: StubBackend) -> None:
    response = httpx.get(f"http://127.0.0.1:{backend.port}/api/v1/private/nope", timeout=30)

    assert response.status_code == 404


# --- the dataset item routes ------------------------------------------------ #


def test_the_items_route_pages_the_dataset_and_takes_filters(backend: StubBackend) -> None:
    backend.suite = CompareSuite(case_count=2_000)
    clause = [{"field": "data", "key": "question", "operator": "contains", "value": "install"}]

    response = _get(backend, _ITEMS, page=2, size=25, filters=json.dumps(clause))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2_000
    assert len(body["content"]) == 25
    assert backend.one("/items").filters() == clause


def test_filters_that_are_not_the_backends_array_are_a_400(backend: StubBackend) -> None:
    """``FiltersFactory`` deserializes the parameter and answers 400 when it
    cannot. A stub that shrugged at a malformed array would let a wrongly
    encoded filter pass every test and fail in production."""
    assert _get(backend, _ITEMS, filters="data.question contains install").status_code == 400


def test_one_case_is_addressed_without_its_dataset(backend: StubBackend) -> None:
    body = _get(backend, f"/v1/private/datasets/items/{CASE_ID}").json()

    assert body["id"] == CASE_ID
    assert body["data"]["expected_answer"] == "Paris"
    assert _get(backend, "/v1/private/datasets/items/not-a-case-id").status_code == 404
