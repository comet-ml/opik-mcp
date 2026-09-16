"""``list('test_suite_item', experiment_ids=[…])`` — the cases, with the runs.

What these pin is the answer an agent reads: which experiment each value in a
cell belongs to, which case is worth opening, and every refusal that is
cheaper to write here than to discover from an empty page. The requests are
pinned too, because the joined endpoint is the one that answers 200 to things
it then ignores.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.entities.test_suite import compare
from opik_mcp.read_list.list_tool import _DEFAULT_SIZE, _MAX_SIZE, run_list

from .test_list_tool import FakeOpikClient

SUITE = "019f8d97-c83c-7597-b40a-bd2e0e1ad558"
OTHER_SUITE = "019f8d97-c83c-7597-b40a-bd2e0e1ad559"
A = "019f8d97-c83c-7597-b40a-00000000000a"
B = "019f8d97-c83c-7597-b40a-00000000000b"
C = "019f8d97-c83c-7597-b40a-00000000000c"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _experiment(
    experiment_id: str,
    name: str,
    *,
    dataset_id: str = SUITE,
    dataset_name: str = "support-qa",
    method: str = "evaluation_suite",
) -> dict[str, Any]:
    return {
        "id": experiment_id,
        "name": name,
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "evaluation_method": method,
    }


def _run(
    experiment_id: str,
    *,
    trace: str = "tr-1",
    scores: dict[str, float] | None = None,
    passed: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "trace_id": trace,
        "feedback_scores": [{"name": k, "value": v} for k, v in (scores or {}).items()],
        "status": "passed" if passed else "failed",
        "assertion_results": [
            {"value": "names the capital", "passed": passed, "reason": reason},
        ],
    }


def _case(
    case_id: str,
    data: dict[str, Any],
    runs: list[dict[str, Any]],
    summaries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "data": data,
        "experiment_items": runs,
        "run_summaries_by_experiment": summaries or {},
    }


def _fake(*cases: dict[str, Any], total: int | None = None, **kw: Any) -> FakeOpikClient:
    return FakeOpikClient(
        experiment_records={
            A: _experiment(A, "baseline-v1"),
            B: _experiment(B, "rerank-v3"),
            C: _experiment(C, "rerank-v4"),
        },
        compared_items={
            "content": list(cases),
            "total": len(cases) if total is None else total,
        },
        **kw,
    )


_DEFAULT_CASE = _case(
    "case-1",
    {"question": "Capital of France?", "expected_answer": "Paris"},
    [
        _run(A, trace="tr-a", scores={"correctness": 0.9}),
        _run(B, trace="tr-b", scores={"correctness": 0.4}, passed=False, reason="Names Lyon."),
    ],
)


# --- the row ---------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_row_is_one_case_with_every_experiments_score_in_one_cell() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    # Case keys keep the plain listing's ranking: fill rate, then name.
    assert "id | data.expected_answer | data.question | correctness" in out
    assert "case-1 | Paris | Capital of France? | 0.9 / 0.4 Δ0.5" in out
    assert "Found 1 test_suite_items (page 1, showing 1 of 1):" in out


@pytest.mark.anyio
async def test_the_legend_names_the_baseline_and_the_order_of_the_values() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert f"E1 = baseline-v1 ({A}); E2 = rerank-v3 ({B})" in out
    assert "E1 is the baseline" in out
    assert "Δ is the unsigned gap between them" in out
    assert out.startswith("[list: test_suite_item | compare: 2 experiments]")


@pytest.mark.anyio
async def test_three_experiments_get_no_delta_and_keep_one_column_per_score() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, scores={"correctness": 0.9}),
                _run(B, scores={"correctness": 0.4}),
                _run(C, scores={"correctness": 0.7}),
            ],
        )
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B, C], client=fake)

    assert "case-1 | Capital? | 0.9 / 0.4 / 0.7" in out
    assert "Δ" not in out
    assert "score cells read E1 / E2 / E3 in that order" in out


@pytest.mark.anyio
async def test_an_experiment_that_did_not_run_a_case_shows_a_dash() -> None:
    fake = _fake(
        _case("case-1", {"question": "Capital?"}, [_run(A, scores={"correctness": 0.9})]),
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Capital? | 0.9 / -" in out


@pytest.mark.anyio
async def test_several_runs_of_one_case_average_into_the_cell() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, scores={"correctness": 1.0}),
                _run(A, scores={"correctness": 0.5}),
                _run(B, scores={"correctness": 0.4}),
            ],
        )
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Capital? | 0.75 / 0.4 Δ0.35" in out


@pytest.mark.anyio
async def test_the_case_keys_are_cut_to_two_and_the_cut_is_stated() -> None:
    """The caller came for the runs; two keys are enough to recognise a case."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?", "expected_answer": "Paris", "locale": "fr", "tier": "gold"},
            [_run(A, scores={"correctness": 0.9}), _run(B, scores={"correctness": 0.4})],
        )
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "id | data.expected_answer | data.locale | correctness" in out
    assert "showing 2 of 4 (the runs take the width); omitted: question, tier." in out


@pytest.mark.anyio
async def test_scores_beyond_four_are_cut_by_fill_rate_and_named_in_the_note() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(
                    A,
                    scores={
                        "correctness": 0.9,
                        "helpfulness": 0.8,
                        "tone": 0.7,
                        "brevity": 0.6,
                        "safety": 0.5,
                        "grounding": 0.4,
                    },
                ),
                _run(B, scores={"correctness": 0.4}),
            ],
        )
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "id | data.question | brevity | correctness | grounding | helpfulness |" in out
    assert "Showing 4 of 6 scores by fill rate; omitted: safety, tone." in out


# --- what the call asks the backend for ------------------------------------- #


@pytest.mark.anyio
async def test_the_suite_comes_from_the_experiments_not_from_the_caller() -> None:
    fake = _fake(_DEFAULT_CASE)

    await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert fake.compare_calls == [
        {
            "test_suite_id": SUITE,
            "experiment_ids": [A, B],
            "filters": None,
            "sorting": None,
            "search": None,
            "page": 1,
            "size": _DEFAULT_SIZE,
        }
    ]


@pytest.mark.anyio
async def test_a_matching_test_suite_id_is_accepted_and_a_wrong_one_is_refused() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("test_suite_item", experiment_ids=[A, B], test_suite_id=SUITE, client=fake)
    assert "case-1" in out

    with pytest.raises(ToolError) as refusal:
        await run_list(
            "test_suite_item", experiment_ids=[A, B], test_suite_id=OTHER_SUITE, client=fake
        )
    assert OTHER_SUITE in str(refusal.value)
    assert "resolved from the experiments" in str(refusal.value)


@pytest.mark.anyio
async def test_experiments_of_different_suites_are_refused_naming_both() -> None:
    fake = _fake(_DEFAULT_CASE)
    fake.experiment_records[B] = _experiment(
        B, "billing-v1", dataset_id=OTHER_SUITE, dataset_name="billing-qa"
    )

    with pytest.raises(ToolError) as refusal:
        await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    message = str(refusal.value)
    assert "support-qa" in message and "billing-qa" in message
    assert fake.compare_calls == []


@pytest.mark.anyio
async def test_the_page_the_caller_asked_for_is_the_page_that_is_fetched() -> None:
    fake = _fake(_DEFAULT_CASE, total=60)

    out = await run_list("test_suite_item", experiment_ids=[A, B], page=2, size=5, client=fake)

    assert fake.compare_calls[0]["page"] == 2
    assert fake.compare_calls[0]["size"] == 5
    assert "Use page=3 for next 5 results." in out


# --- refusals --------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        ([""], "non-empty array"),
        ([f"e-{n}" for n in range(11)], "Compare up to 10"),
        ([A, A], "repeats an experiment"),
    ],
)
async def test_the_experiment_ids_a_call_cannot_mean_are_refused(
    ids: list[str], expected: str
) -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("test_suite_item", experiment_ids=ids, client=fake)

    assert expected in str(refusal.value)
    assert fake.compare_calls == []


@pytest.mark.anyio
async def test_a_filter_without_experiment_ids_says_what_it_needs() -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("test_suite_item", test_suite_id=SUITE, filters="duration > 1", client=fake)

    assert "filters on test_suite_item need experiment_ids" in str(refusal.value)


@pytest.mark.anyio
async def test_a_sort_without_experiment_ids_says_what_it_needs() -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("test_suite_item", test_suite_id=SUITE, sort="duration desc", client=fake)

    assert "sort on test_suite_item need experiment_ids" in str(refusal.value)


@pytest.mark.anyio
async def test_a_time_window_is_refused_because_a_case_has_none() -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("test_suite_item", experiment_ids=[A, B], since="7d", client=fake)

    assert "since/until are not supported" in str(refusal.value)


@pytest.mark.anyio
async def test_an_empty_comparison_says_why_it_could_be_empty() -> None:
    fake = _fake()

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "No cases found" in out
    assert "E1 = baseline-v1" in out


# --- the plain listing is untouched ----------------------------------------- #


@pytest.mark.anyio
async def test_without_experiment_ids_the_list_is_still_the_suites_cases() -> None:
    fake = FakeOpikClient(
        test_suite_items={
            "content": [{"id": "i-1", "data": {"question": "Capital?", "answer": "Paris"}}],
            "total": 1,
        }
    )

    out = await run_list("test_suite_item", test_suite_id=SUITE, client=fake)

    assert fake.last_kwargs == {"test_suite_id": SUITE, "page": 1, "size": _DEFAULT_SIZE}
    assert "i-1 | Paris | Capital?" in out
    assert fake.compare_calls == []


def test_the_comparisons_page_defaults_are_the_list_tools() -> None:
    """Two modules cannot disagree about what ``size`` means by default."""
    assert (compare.DEFAULT_SIZE, compare.MAX_SIZE) == (_DEFAULT_SIZE, _MAX_SIZE)


# --- the columns only a test suite has -------------------------------------- #


def _summaries(**per_experiment: tuple[int, int]) -> dict[str, Any]:
    return {
        experiment_id: {
            "passed_runs": passed,
            "total_runs": total,
            "status": "passed" if passed == total else "failed",
        }
        for experiment_id, (passed, total) in per_experiment.items()
    }


_REGRESSED = _case(
    "case-1",
    {"question": "Capital?"},
    [
        _run(A, trace="tr-a", scores={"correctness": 0.9}),
        _run(B, trace="tr-b", scores={"correctness": 0.4}, passed=False, reason="Names Lyon."),
    ],
    summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (0, 1)})},
)


@pytest.mark.anyio
async def test_a_suite_row_carries_pass_state_the_worst_trace_and_the_reason() -> None:
    fake = _fake(_REGRESSED)

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "passed | worst_trace | reason" in out
    assert "1/1·0/1 | tr-b (E2) | Names Lyon." in out
    assert "passed is passed/total runs, E1·E2." in out


@pytest.mark.anyio
async def test_experiments_over_a_plain_dataset_get_no_suite_columns() -> None:
    """``evaluate()`` runs have no assertions, so a pass column could only ever
    be empty — and an empty column reads like a failure to record, not like a
    column that does not apply."""
    fake = _fake(_REGRESSED)
    fake.experiment_records[A] = _experiment(A, "baseline-v1", method="dataset")
    fake.experiment_records[B] = _experiment(B, "rerank-v3", method="dataset")

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "passed" not in out
    assert "worst_trace" not in out
    assert "reason" not in out
    assert "0.9 / 0.4" in out


@pytest.mark.anyio
async def test_the_worst_trace_is_a_run_that_actually_failed() -> None:
    """An experiment whose policy ran the case twice has a trace that shows
    the failure and one that shows nothing."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(B, trace="tr-b-pass", scores={"correctness": 0.9}),
                _run(
                    B,
                    trace="tr-b-fail",
                    scores={"correctness": 0.1},
                    passed=False,
                    reason="Names Lyon.",
                ),
            ],
            summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (1, 2)})},
        )
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "1/1·1/2 | tr-b-fail (E2) | Names Lyon." in out


@pytest.mark.anyio
async def test_a_case_every_run_passed_names_a_trace_and_no_reason() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(B, trace="tr-b", scores={"correctness": 0.9}),
            ],
            summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (1, 1)})},
        )
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    # Ties go to the newer run: the baseline's trace is the one already known.
    assert out.splitlines()[4].endswith("1/1·1/1 | tr-b (E2) | ")


@pytest.mark.anyio
async def test_a_row_without_run_summaries_or_assertions_still_renders() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [{"experiment_id": A, "trace_id": "tr-a"}, {"experiment_id": B, "trace_id": "tr-b"}],
        )
    )

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Capital? | -·- | tr-b (E2) | " in out
