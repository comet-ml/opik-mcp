"""``list('test_suite_item', experiment_ids=[…])`` — the cases, with the runs.

What these pin is the answer an agent reads: which experiment each value in a
cell belongs to, which case is worth opening, and every refusal that is
cheaper to write here than to discover from an empty page. The requests are
pinned too, because the joined endpoint is the one that answers 200 to things
it then ignores.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.opik_client import OpikServerError
from opik_mcp.read_list.entities.test_suite import compare
from opik_mcp.read_list.list_tool import _DEFAULT_SIZE, _MAX_SIZE, run_list
from opik_mcp.read_list.reference import list_reference

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


# --- filters ---------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_filter_on_the_runs_puts_the_hidden_experiments_back() -> None:
    """The backend strips the runs that did not match. A row showing only the
    failing run cannot tell a regression from a case that was always bad."""
    stripped = _case(
        "case-1",
        {"question": "Capital?"},
        [_run(B, trace="tr-b", scores={"correctness": 0.4}, passed=False, reason="Names Lyon.")],
    )
    fake = _fake(stripped)
    fake.compared_items = {"content": [stripped], "total": 1}

    out = await run_list(
        "test_suite_item",
        experiment_ids=[A, B],
        filters="feedback_scores.correctness < 0.5",
        client=fake,
    )

    assert len(fake.compare_calls) == 2
    first, refetch = fake.compare_calls
    assert json.loads(first["filters"]) == [
        {"field": "feedback_scores", "operator": "<", "key": "correctness", "value": "0.5"}
    ]
    assert json.loads(refetch["filters"]) == [
        {"field": "id", "operator": "=", "key": "", "value": "case-1"}
    ]
    assert refetch["size"] == 1
    assert "matches a case when any of its experiments matches" in out


@pytest.mark.anyio
async def test_a_filter_on_the_case_needs_no_second_call() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list(
        "test_suite_item",
        experiment_ids=[A, B],
        filters='data.question contains "Capital"',
        client=fake,
    )

    assert len(fake.compare_calls) == 1
    assert json.loads(fake.compare_calls[0]["filters"]) == [
        {
            "field": "data.question",
            "type": "string",
            "operator": "contains",
            "key": "",
            "value": "Capital",
        }
    ]
    assert "matches a case when any" not in out


@pytest.mark.anyio
async def test_a_refetch_that_fails_keeps_the_row_it_could_not_complete() -> None:
    fake = _fake(_DEFAULT_CASE)
    calls: list[dict[str, Any]] = []

    async def one_good_then_broken(test_suite_id: str, /, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        if len(calls) == 1:
            return fake.compared_items
        raise OpikServerError("refetch exploded (500).")

    fake.list_compared_test_suite_items = one_good_then_broken  # type: ignore[method-assign]

    out = await run_list(
        "test_suite_item",
        experiment_ids=[A, B],
        filters="feedback_scores.correctness < 0.5",
        client=fake,
    )

    assert "case-1" in out
    assert "1 row could not be fetched again" in out


@pytest.mark.anyio
async def test_a_run_filter_over_a_wide_page_is_refused_before_it_fans_out() -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list(
            "test_suite_item",
            experiment_ids=[A, B],
            filters="feedback_scores.correctness < 0.5",
            size=26,
            client=fake,
        )

    assert "use size=25 or less" in str(refusal.value)
    assert fake.compare_calls == []

    out = await run_list(
        "test_suite_item",
        experiment_ids=[A, B],
        filters="feedback_scores.correctness < 0.5",
        size=25,
        client=fake,
    )
    assert "case-1" in out


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unapplied",
    ["total_estimated_cost > 0.01", "usage.total_tokens > 100"],
)
async def test_filters_the_backend_accepts_and_ignores_are_refused(unapplied: str) -> None:
    """Both pass the joined endpoint's validation and never reach its query, so
    the page comes back unfiltered and reads as filtered."""
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("test_suite_item", experiment_ids=[A, B], filters=unapplied, client=fake)

    message = str(refusal.value)
    assert "Unknown field" in message
    assert "answers 200 and never applies it" in message
    assert "comments, data, duration, feedback_scores, id, output" in message
    assert fake.compare_calls == []


def test_the_schema_publishes_the_fields_a_comparison_can_filter_and_sort_on() -> None:
    reference = list_reference("test_suite_item")

    assert sorted(reference["filters"]["fields"]) == [
        "comments",
        "data",
        "duration",
        "feedback_scores",
        "id",
        "output",
    ]
    assert reference["filters"]["fields"]["data"]["key"] == "required"
    assert reference["filters"]["fields"]["output"]["key"] == "optional"
    assert "status" not in reference["sort"]["fields"]
    assert "experiment_ids" in reference["filters"]["requires"]


# --- sort ------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_sort_the_backend_orders_by_is_sent_and_echoed() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list(
        "test_suite_item",
        experiment_ids=[A, B],
        sort="feedback_scores.correctness asc",
        client=fake,
    )

    assert json.loads(fake.compare_calls[0]["sorting"]) == [
        {"field": "feedback_scores.correctness", "direction": "ASC"}
    ]
    assert "sort: feedback_scores.correctness asc" in out.splitlines()[0]


@pytest.mark.anyio
@pytest.mark.parametrize("dropped", ["status desc", "passed asc", "reason asc"])
async def test_a_sort_the_backend_would_drop_is_refused_before_the_call(dropped: str) -> None:
    """opik-backend logs an unsupported sort field and answers 200 with an
    unsorted page, which reads exactly like a sorted one."""
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("test_suite_item", experiment_ids=[A, B], sort=dropped, client=fake)

    message = str(refusal.value)
    assert "feedback_scores.<name>" in message
    assert fake.compare_calls == []


@pytest.mark.anyio
async def test_sorting_by_a_case_key_or_an_output_key_is_allowed() -> None:
    fake = _fake(_DEFAULT_CASE)

    for sort in ("data.question desc", "output.answer asc", "duration desc"):
        await run_list("test_suite_item", experiment_ids=[A, B], sort=sort, client=fake)

    assert [json.loads(call["sorting"])[0]["field"] for call in fake.compare_calls] == [
        "data.question",
        "output.answer",
        "duration",
    ]


# --- what the first page says about what can be asked next ------------------ #


def _columns(*names: str) -> dict[str, Any]:
    return {"columns": [{"name": name, "types": ["string"]} for name in names]}


@pytest.mark.anyio
async def test_the_first_page_names_the_output_keys_and_the_case_keys() -> None:
    fake = _fake(_DEFAULT_CASE, compared_columns=_columns("input", "answer", "reasoning"))

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    # ``input`` is the case echoed back by a suite run, not an output of it.
    assert "runs' output keys: answer, reasoning" in out
    assert "case data keys: expected_answer, question" in out
    assert "output.<key> and data.<key>" in out
    assert len(fake.column_calls) == 1


@pytest.mark.anyio
async def test_a_plain_dataset_keeps_its_input_output_key() -> None:
    fake = _fake(_DEFAULT_CASE, compared_columns=_columns("input", "answer"))
    fake.experiment_records[A] = _experiment(A, "baseline-v1", method="dataset")
    fake.experiment_records[B] = _experiment(B, "rerank-v3", method="dataset")

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "runs' output keys: input, answer" in out


@pytest.mark.anyio
async def test_the_second_page_does_not_ask_for_the_output_keys_again() -> None:
    fake = _fake(_DEFAULT_CASE, total=60, compared_columns=_columns("answer"))

    out = await run_list("test_suite_item", experiment_ids=[A, B], page=2, client=fake)

    assert fake.column_calls == []
    assert "output keys" not in out


@pytest.mark.anyio
async def test_a_failed_output_columns_call_costs_the_line_not_the_page() -> None:
    fake = _fake(_DEFAULT_CASE, columns_error=OpikServerError("columns exploded (500)."))

    out = await run_list("test_suite_item", experiment_ids=[A, B], client=fake)

    assert "case-1" in out
    assert "output keys" not in out
    assert "case data keys: expected_answer, question" in out


@pytest.mark.anyio
async def test_a_search_says_which_half_of_the_row_it_matched() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("test_suite_item", experiment_ids=[A, B], search="Capital", client=fake)

    assert fake.compare_calls[0]["search"] == "Capital"
    assert 'search: "Capital"' in out.splitlines()[0]
    assert "matched the cases' data, not the runs' output" in out
