"""``list('dataset_item', experiment_ids=[…])`` — the cases, with the runs.

What these pin is the answer an agent reads: which experiment each value in a
cell belongs to, which case is worth opening, and every refusal that is
cheaper to write here than to discover from an empty page. The requests are
pinned too, because the joined endpoint is the one that answers 200 to things
it then ignores.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.opik_client import OpikServerError
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.paging import DEFAULT_PAGE_SIZE
from opik_mcp.read_list.reference import list_reference

from .test_list_tool import FakeOpikClient

DATASET = "019f8d97-c83c-7597-b40a-bd2e0e1ad558"
OTHER_DATASET = "019f8d97-c83c-7597-b40a-bd2e0e1ad559"
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
    dataset_id: str = DATASET,
    dataset_name: str = "support-qa",
    method: str = "evaluation_suite",
    version: tuple[str, str] = ("dv-1", "v1"),
    status: str = "completed",
    trace_count: int = 20,
) -> dict[str, Any]:
    version_id, version_name = version
    return {
        "id": experiment_id,
        "name": name,
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "evaluation_method": method,
        "dataset_version_id": version_id,
        "dataset_version_summary": {"id": version_id, "version_name": version_name},
        "status": status,
        "trace_count": trace_count,
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
            A: _experiment(A, "rerank-v1"),
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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    # Case keys keep the plain listing's ranking: fill rate, then name.
    assert "id | data.expected_answer | data.question | correctness" in out
    assert "case-1 | Paris | Capital of France? | 0.9 / 0.4 Δ-0.5" in out
    assert "Found 1 dataset_items (page 1, showing 1 of 1):" in out


@pytest.mark.anyio
async def test_the_legend_names_the_baseline_and_the_order_of_the_values() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    # The labels are the key to every cell, so they are above the table, with
    # the ids the caller's next call is written with — not in a note below it.
    assert out.startswith(
        f"[list: dataset_item | compare: E1 = baseline rerank-v1 ({A}), E2 = rerank-v3 ({B})]"
    )
    assert "E1 is the baseline" in out
    assert "Δ is E2 minus E1 (a + means E2 scored higher)" in out


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

    out = await run_list("dataset_item", experiment_ids=[A, B, C], client=fake)

    assert "case-1 | Capital? | 0.9 / 0.4 / 0.7" in out
    assert "Δ" not in out
    assert "score cells read E1 / E2 / E3 in that order" in out


@pytest.mark.anyio
async def test_an_experiment_that_did_not_run_a_case_shows_a_dash() -> None:
    fake = _fake(
        _case("case-1", {"question": "Capital?"}, [_run(A, scores={"correctness": 0.9})]),
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Capital? | 0.75 / 0.4 Δ-0.35" in out


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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    # Only ``correctness`` carries a Δ: the rest are E1's alone, so they are
    # the columns with no sign to misread (see the direction section below).
    assert (
        "id | data.question | brevity | correctness (direction unknown) | grounding | helpfulness |"
    ) in out
    assert "Showing 4 of 6 scores by fill rate; omitted: safety, tone." in out


# --- what the call asks the backend for ------------------------------------- #


@pytest.mark.anyio
async def test_the_dataset_comes_from_the_experiments_not_from_the_caller() -> None:
    fake = _fake(_DEFAULT_CASE)

    await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert fake.compare_calls == [
        {
            "dataset_id": DATASET,
            "experiment_ids": [A, B],
            "filters": None,
            "sorting": None,
            "search": None,
            "page": 1,
            "size": DEFAULT_PAGE_SIZE,
        }
    ], f"the compare call differs: {fake.compare_calls}"


@pytest.mark.anyio
async def test_a_matching_dataset_id_is_accepted_and_a_wrong_one_is_refused() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("dataset_item", experiment_ids=[A, B], dataset_id=DATASET, client=fake)
    assert "case-1" in out

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", experiment_ids=[A, B], dataset_id=OTHER_DATASET, client=fake)
    assert OTHER_DATASET in str(refusal.value)
    assert "resolved from the experiments" in str(refusal.value)


@pytest.mark.anyio
async def test_experiments_of_different_datasets_are_refused_naming_both() -> None:
    fake = _fake(_DEFAULT_CASE)
    fake.experiment_records[B] = _experiment(
        B, "billing-v1", dataset_id=OTHER_DATASET, dataset_name="billing-qa"
    )

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    message = str(refusal.value)
    assert "support-qa" in message and "billing-qa" in message
    assert fake.compare_calls == []


# --- whether the table can be read at face value ---------------------------- #
#
# Each of these was a check the agent had to make for itself, with a
# list('experiment') call before comparing — and skipped, because the table
# renders either way. Driving the tool: a "winner" at 0.634 over three cases
# had lost one of them, and nothing on the page said three.


@pytest.mark.anyio
async def test_runs_over_different_dataset_versions_are_compared_with_a_warning() -> None:
    """Same dataset, different version: the shared cases still line up, so it
    is not the refusal a different dataset gets — but a - may mean the case
    did not exist yet, and a gap on an edited case is not a regression."""
    fake = _fake(_DEFAULT_CASE)
    fake.experiment_records[B] = _experiment(B, "rerank-v3", version=("dv-2", "v2"))
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "different versions of the dataset" in out
    assert "E1 ran v1" in out and "E2 ran v2" in out
    assert "case-1" in out, "the table still renders"
    assert out.index("different versions") < out.index("E1 is the baseline"), (
        "the warning comes before the explanation of how to read the cells"
    )


@pytest.mark.anyio
async def test_a_run_still_running_is_flagged_as_a_moving_number() -> None:
    fake = _fake(_DEFAULT_CASE)
    fake.experiment_records[B] = _experiment(B, "rerank-v3", status="running")
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "E2 is still running" in out
    assert "will change" in out


@pytest.mark.anyio
async def test_runs_over_different_numbers_of_cases_say_so_and_name_the_thin_one() -> None:
    fake = _fake(_DEFAULT_CASE)
    fake.experiment_records[B] = _experiment(B, "rerank-v3", trace_count=3)
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "different numbers of cases (E1 20, E2 3)" in out
    assert "3 is too few" in out


@pytest.mark.anyio
async def test_runs_that_differ_only_in_size_but_both_large_get_the_count_not_the_verdict() -> None:
    fake = _fake(_DEFAULT_CASE)
    fake.experiment_records[B] = _experiment(B, "rerank-v3", trace_count=40)
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "(E1 20, E2 40)" in out
    assert "too few" not in out


@pytest.mark.anyio
async def test_comparable_runs_get_no_warning_at_all() -> None:
    """Same version, both finished, same size: nothing to say, and saying
    nothing is how the reader learns that a note means something."""
    out = await run_list("dataset_item", experiment_ids=[A, B], client=_fake(_DEFAULT_CASE))
    for phrase in ("different versions", "still running", "different numbers of cases"):
        assert phrase not in out


@pytest.mark.anyio
async def test_the_page_the_caller_asked_for_is_the_page_that_is_fetched() -> None:
    fake = _fake(_DEFAULT_CASE, total=60)

    out = await run_list("dataset_item", experiment_ids=[A, B], page=2, size=5, client=fake)

    assert fake.compare_calls[0]["page"] == 2
    assert fake.compare_calls[0]["size"] == 5
    assert "Use page=3 for next 5 results." in out


# --- refusals --------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        ([""], "non-empty array"),
        # An empty array is an argument the caller passed, so it reaches the
        # runner: the refusal has to be about what is in it, not about it
        # being absent.
        ([], "drop the argument"),
        ([f"e-{n}" for n in range(11)], "Compare up to 10"),
        ([A, A], "repeats an experiment"),
    ],
)
async def test_the_experiment_ids_a_call_cannot_mean_are_refused(
    ids: list[str], expected: str
) -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", experiment_ids=ids, client=fake)

    assert expected in str(refusal.value)
    assert fake.compare_calls == []


@pytest.mark.anyio
async def test_a_filter_on_the_runs_without_experiment_ids_says_where_they_are() -> None:
    """Without the experiments this is a plain list of the dataset's cases,
    which takes filters of its own (OPIK-8397) — but not these: ``duration``
    is a run's, and there are no runs on the page."""
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", dataset_id=DATASET, filters="duration > 1", client=fake)

    message = str(refusal.value)
    assert "Unknown field 'duration'" in message
    assert "It is a field of the comparison's joined page" in message
    assert "experiment_ids" in message
    assert fake.compare_calls == []


@pytest.mark.anyio
async def test_a_sort_without_experiment_ids_says_what_orders_cases() -> None:
    """The items endpoint takes no sorting parameter at all, so the only
    ordering of cases is the comparison's."""
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", dataset_id=DATASET, sort="duration desc", client=fake)

    message = str(refusal.value)
    assert "sort is not supported" in message
    assert "a sort needs experiment_ids" in message
    assert fake.compare_calls == []


@pytest.mark.anyio
async def test_a_time_window_is_refused_because_a_case_has_none() -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", experiment_ids=[A, B], since="7d", client=fake)

    assert "since/until are not supported" in str(refusal.value)


@pytest.mark.anyio
async def test_an_empty_comparison_says_why_it_could_be_empty() -> None:
    fake = _fake()

    out = await run_list("dataset_item", experiment_ids=[A, B], search="nothing", client=fake)

    assert "No case matched the search" in out
    assert f"E1 = baseline rerank-v1 ({A})" in out
    # An empty page is a page to ask a second question from.
    assert "matched the cases' data, not the runs' output" in out


@pytest.mark.anyio
async def test_an_empty_page_past_the_end_says_so_rather_than_blaming_the_runs() -> None:
    """Live, page 2 of a 3-case suite claimed the experiments had no cases in
    common — which would have been a real problem, and was not one."""
    fake = _fake(total=3)

    out = await run_list("dataset_item", experiment_ids=[A, B], page=2, size=25, client=fake)

    assert "Page 2 is past the end: the comparison has 3 cases." in out
    assert "no items in common" not in out


@pytest.mark.anyio
async def test_a_case_filter_that_matches_nothing_does_not_explain_the_runs() -> None:
    """The any-run sentence answers a question this caller did not ask."""
    fake = _fake()

    out = await run_list(
        "dataset_item",
        experiment_ids=[A, B],
        filters='data.question contains "nothing"',
        client=fake,
    )

    assert "No case matched the filter." in out
    assert "any of its experiments" not in out


# --- the plain listing is untouched ----------------------------------------- #


def _items(data: dict[str, Any]) -> dict[str, Any]:
    """One page of the plain listing: a single case with the given data map."""
    return {"content": [{"id": "i-1", "data": data}], "total": 1}


@pytest.mark.anyio
async def test_without_experiment_ids_the_list_is_still_the_datasets_cases() -> None:
    fake = FakeOpikClient(
        dataset_items={
            "content": [{"id": "i-1", "data": {"question": "Capital?", "answer": "Paris"}}],
            "total": 1,
        }
    )

    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)

    assert fake.last_kwargs == {"dataset_id": DATASET, "page": 1, "size": DEFAULT_PAGE_SIZE}
    assert "i-1 | Paris | Capital?" in out
    assert fake.compare_calls == []


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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "passed | worst_trace | reason" in out
    assert "1/1·0/1 | tr-b (E2) | names the capital: Names Lyon." in out
    assert "passed is passed/total runs, E1·E2." in out


@pytest.mark.anyio
async def test_experiments_over_a_plain_dataset_get_no_suite_columns() -> None:
    """``evaluate()`` runs have no assertions, so a pass column could only ever
    be empty — and an empty column reads like a failure to record, not like a
    column that does not apply."""
    fake = _fake(_REGRESSED)
    fake.experiment_records[A] = _experiment(A, "rerank-v1", method="dataset")
    fake.experiment_records[B] = _experiment(B, "rerank-v3", method="dataset")

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "passed" not in out
    assert "reason" not in out
    # The run worth opening next is not a test suite's privilege.
    assert "correctness (direction unknown) | worst_trace" in out
    assert "0.9 / 0.4 Δ-0.5 | tr-b (E2)" in out


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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "1/1·1/2 | tr-b-fail (E2) | names the capital: Names Lyon." in out


@pytest.mark.anyio
async def test_the_worst_run_is_the_worst_run_not_the_worst_average() -> None:
    """An experiment that ran the case three times and blew one of them is
    where the failure is, even though its average beats the other run's."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a1", scores={"correctness": 1.0}),
                _run(A, trace="tr-a2", scores={"correctness": 1.0}),
                _run(
                    A,
                    trace="tr-a3",
                    scores={"correctness": 0.5},
                    passed=False,
                    reason="Names Lyon.",
                ),
                _run(B, trace="tr-b", scores={"correctness": 0.7}),
            ],
            summaries={**_summaries(**{A: (2, 3)}), **_summaries(**{B: (1, 1)})},
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    # A averages 0.83 against B's 0.7, so an average would open B's trace and
    # show nothing. The lowest experiment item is A's third run.
    assert "2/3·1/1 | tr-a3 (E1) | names the capital: Names Lyon." in out


@pytest.mark.anyio
async def test_with_no_failed_run_the_worst_trace_is_the_lowest_scoring_one() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a-high", scores={"correctness": 0.9}),
                _run(A, trace="tr-a-low", scores={"correctness": 0.2}),
                _run(B, trace="tr-b", scores={"correctness": 0.95}),
            ],
            summaries={**_summaries(**{A: (2, 2)}), **_summaries(**{B: (1, 1)})},
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "tr-a-low (E1)" in out


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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    # No scores, no summaries, no failed run: nothing ranks the experiments,
    # so no trace is called the worst one.
    assert "case-1 | Capital? | -·- | - | -" in out


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
        "dataset_item",
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
        "dataset_item",
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
    ], f"the compiled compare filter differs: {fake.compare_calls[0]['filters']}"
    assert "matches a case when any" not in out


@pytest.mark.anyio
async def test_a_refetch_that_fails_keeps_the_row_it_could_not_complete() -> None:
    fake = _fake(_DEFAULT_CASE)
    calls: list[dict[str, Any]] = []

    async def one_good_then_broken(dataset_id: str, /, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        if len(calls) == 1:
            return fake.compared_items
        raise OpikServerError("refetch exploded (500).")

    fake.list_compared_dataset_items = one_good_then_broken  # type: ignore[method-assign]

    out = await run_list(
        "dataset_item",
        experiment_ids=[A, B],
        filters="feedback_scores.correctness < 0.5",
        client=fake,
    )

    assert "case-1" in out
    # Naming the row makes that row suspect; a bare count makes the page suspect.
    assert (
        "1 row could not be fetched again and shows only the runs that matched "
        "the filter: case-1." in out
    )


@pytest.mark.anyio
async def test_a_backend_having_a_bad_minute_does_not_fill_the_note_with_ids() -> None:
    """A capped page is 25 rows; 25 uuids of apology is not a note."""
    cases = [
        _case(f"case-{n}", {"question": "Capital?"}, [_run(B, scores={"correctness": 0.4})])
        for n in range(1, 6)
    ]
    fake = _fake(*cases)
    calls: list[dict[str, Any]] = []

    async def one_good_then_broken(dataset_id: str, /, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        if len(calls) == 1:
            return fake.compared_items
        raise OpikServerError("refetch exploded (500).")

    fake.list_compared_dataset_items = one_good_then_broken  # type: ignore[method-assign]

    out = await run_list(
        "dataset_item",
        experiment_ids=[A, B],
        filters="feedback_scores.correctness < 0.5",
        client=fake,
    )

    assert "the filter: case-1, case-2, case-3, and 2 more." in out


@pytest.mark.anyio
async def test_a_run_filter_over_a_wide_page_is_refused_before_it_fans_out() -> None:
    fake = _fake(_DEFAULT_CASE)

    with pytest.raises(ToolError) as refusal:
        await run_list(
            "dataset_item",
            experiment_ids=[A, B],
            filters="feedback_scores.correctness < 0.5",
            size=26,
            client=fake,
        )

    assert "use size=25 or less" in str(refusal.value)
    assert fake.compare_calls == []

    out = await run_list(
        "dataset_item",
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
        await run_list("dataset_item", experiment_ids=[A, B], filters=unapplied, client=fake)

    message = str(refusal.value)
    assert "Unknown field" in message
    assert "answers 200 and never applies it" in message
    assert "comments, data, duration, feedback_scores, id, output" in message
    assert fake.compare_calls == []


def test_the_schema_publishes_the_fields_a_comparison_can_filter_and_sort_on() -> None:
    reference = list_reference("dataset_item")

    assert sorted(reference["filters"]["fields"]) == [
        "comments",
        "data",
        "duration",
        "feedback_scores",
        "id",
        "output",
    ], f"compare filter fields changed: {sorted(reference['filters']['fields'])}"
    assert reference["filters"]["fields"]["data"]["key"] == "required"
    assert reference["filters"]["fields"]["output"]["key"] == "optional"
    assert "status" not in reference["sort"]["fields"]
    assert "experiment_ids" in reference["filters"]["requires"]


# --- sort ------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_sort_the_backend_orders_by_is_sent_and_echoed() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list(
        "dataset_item",
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
        await run_list("dataset_item", experiment_ids=[A, B], sort=dropped, client=fake)

    message = str(refusal.value)
    assert "feedback_scores.<name>" in message
    assert fake.compare_calls == []


@pytest.mark.anyio
async def test_sorting_by_a_case_key_or_an_output_key_is_allowed() -> None:
    fake = _fake(_DEFAULT_CASE)

    for sort in ("data.question desc", "output.answer asc", "duration desc"):
        await run_list("dataset_item", experiment_ids=[A, B], sort=sort, client=fake)

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

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    # ``input`` is the case echoed back by a suite run, not an output of it.
    assert "runs' output keys: answer, reasoning" in out
    assert "case data keys: expected_answer, question" in out
    assert "output.<key> and data.<key>" in out
    assert len(fake.column_calls) == 1


@pytest.mark.anyio
async def test_a_plain_dataset_keeps_its_input_output_key() -> None:
    fake = _fake(_DEFAULT_CASE, compared_columns=_columns("input", "answer"))
    fake.experiment_records[A] = _experiment(A, "rerank-v1", method="dataset")
    fake.experiment_records[B] = _experiment(B, "rerank-v3", method="dataset")

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "runs' output keys: input, answer" in out


@pytest.mark.anyio
async def test_the_second_page_does_not_ask_for_the_output_keys_again() -> None:
    fake = _fake(_DEFAULT_CASE, total=60, compared_columns=_columns("answer"))

    out = await run_list("dataset_item", experiment_ids=[A, B], page=2, client=fake)

    assert fake.column_calls == []
    assert "output keys" not in out


@pytest.mark.anyio
async def test_a_failed_output_columns_call_costs_the_line_not_the_page() -> None:
    fake = _fake(_DEFAULT_CASE, columns_error=OpikServerError("columns exploded (500)."))

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "case-1" in out
    assert "output keys" not in out
    assert "case data keys: expected_answer, question" in out


@pytest.mark.anyio
async def test_a_search_says_which_half_of_the_row_it_matched() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("dataset_item", experiment_ids=[A, B], search="Capital", client=fake)

    assert fake.compare_calls[0]["search"] == "Capital"
    assert 'search: "Capital"' in out.splitlines()[0]
    assert "matched the cases' data, not the runs' output" in out


@pytest.mark.anyio
async def test_a_sort_says_which_of_the_runs_it_actually_ordered_by() -> None:
    """A joined row has one value per column and several runs behind it, so
    the backend averages the numbers and takes the bodies from the newest run.
    A caller ranking regressions would otherwise read the order as the
    baseline's."""
    fake = _fake(_DEFAULT_CASE)

    averaged = await run_list(
        "dataset_item",
        experiment_ids=[A, B],
        sort="feedback_scores.correctness asc",
        client=fake,
    )
    newest = await run_list(
        "dataset_item", experiment_ids=[A, B], sort="output.answer asc", client=fake
    )
    case_level = await run_list(
        "dataset_item", experiment_ids=[A, B], sort="created_at desc", client=fake
    )

    assert "averaged across the compared runs" in averaged
    assert "not by the baseline's own value" in averaged
    assert "the most recent run's value on each case" in newest
    # One value per row: nothing to warn about.
    assert "ordered the page by" not in case_level


# --- the shapes review found on live data ----------------------------------- #


def _assertion_only_run(
    experiment_id: str, *, trace: str, passed: bool = True, reason: str | None = None
) -> dict[str, Any]:
    """A run judged by assertions alone: ``feedback_scores`` is null, as the
    backend sends it for a suite that scores nothing numerically."""
    return {
        "experiment_id": experiment_id,
        "trace_id": trace,
        "feedback_scores": None,
        "status": "passed" if passed else "failed",
        "assertion_results": [{"value": "Names the capital", "passed": passed, "reason": reason}],
    }


@pytest.mark.anyio
async def test_a_suite_judged_by_assertions_alone_says_so_and_names_no_worst_run() -> None:
    """Every run passed and none scored anything, so nothing distinguishes the
    experiments. A worst trace here would be a ranking nobody made, and a
    table with no score columns needs to say why."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [_assertion_only_run(A, trace="tr-a"), _assertion_only_run(B, trace="tr-b")],
            summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (1, 1)})},
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Capital? | 1/1·1/1 | - | " in out
    assert "recorded no feedback scores" in out
    assert "judged by assertions" in out


@pytest.mark.anyio
async def test_a_failed_assertion_run_is_still_the_worst_trace_without_scores() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _assertion_only_run(A, trace="tr-a"),
                _assertion_only_run(B, trace="tr-b", passed=False, reason="Names Lyon."),
            ],
            summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (0, 1)})},
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "1/1·0/1 | tr-b (E2) | Names the capital: Names Lyon." in out


@pytest.mark.anyio
async def test_a_page_with_score_columns_does_not_claim_there_are_none() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "recorded no feedback scores" not in out


@pytest.mark.anyio
async def test_a_case_one_experiment_never_ran_is_counted_so_its_dash_reads_right() -> None:
    """``0 / -`` looks like a missing score. When the experiment has no run on
    the case at all, the page has to say that is what the dash means."""
    fake = _fake(
        _case("case-1", {"question": "Week?"}, [_run(A, trace="tr-a", scores={"strict": 0.0})]),
        _case(
            "case-2",
            {"question": "Sum?"},
            [
                _run(A, trace="tr-a2", scores={"strict": 0.0}),
                _run(B, trace="tr-b2", scores={"strict": 0.0}),
            ],
        ),
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Week? | 0 / -" in out
    assert "1 of 2 cases was not run by every experiment" in out
    assert "a - there means no run, not a zero score" in out


@pytest.mark.anyio
async def test_every_experiment_running_every_case_needs_no_such_note() -> None:
    fake = _fake(_DEFAULT_CASE)

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "not run by every experiment" not in out


@pytest.mark.anyio
async def test_a_reason_with_line_breaks_and_a_pipe_stays_one_cell() -> None:
    """A judge writes prose. The first real reason with a paragraph break
    would otherwise split the row, and a ``|`` would add a column."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Line one\nLine two | with a pipe"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(
                    B,
                    trace="tr-b",
                    scores={"correctness": 0.1},
                    passed=False,
                    reason="The answer names Lyon.\n\nParis | expected.\r\nSee trace.",
                ),
            ],
            summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (0, 1)})},
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    rows = [line for line in out.splitlines() if line.startswith("case-1")]
    assert len(rows) == 1, "the row must stay on one line"
    row = rows[0]
    assert row.count(" | ") == 5, "id, question, score, passed, worst_trace, reason"
    assert "Line one Line two ¦ with a pipe" in row
    assert "The answer names Lyon. Paris ¦ expected. See trace." in row


# --- the direction nobody records (OPIK-8394) ------------------------------- #


@pytest.mark.anyio
async def test_a_score_column_that_carries_a_delta_says_the_direction_is_unknown() -> None:
    """Opik records no direction for a score — a numerical definition carries
    a min and a max and nothing else — so the sign of Δ is arithmetic. The
    column header says so, where the Δ is read, not only in a note under it."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"hallucination": 0.2}),
                _run(B, trace="tr-b", scores={"hallucination": 0.9}),
            ],
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "id | data.question | hallucination (direction unknown) | passed" in out
    assert "case-1 | Capital? | 0.2 / 0.9 Δ+0.7" in out
    assert "No score definition records which direction is better" in out
    assert "on a lower-is-better metric a + is the regression" in out


@pytest.mark.anyio
async def test_one_experiment_has_no_delta_so_its_score_column_is_unmarked() -> None:
    """The marker belongs to the Δ. With one run there is no subtraction, so
    the header carries the score's name and nothing to distrust."""
    fake = _fake(_case("case-1", {"question": "Capital?"}, [_run(A, scores={"correctness": 0.9})]))

    out = await run_list("dataset_item", experiment_ids=[A], client=fake)

    assert "id | data.question | correctness | passed" in out
    assert "direction unknown" not in out


@pytest.mark.anyio
async def test_a_categorical_column_is_unmarked_because_a_label_has_no_delta() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                {
                    "experiment_id": A,
                    "trace_id": "tr-a",
                    "feedback_scores": [{"name": "verdict", "value": 0, "category_name": "low"}],
                },
                {
                    "experiment_id": B,
                    "trace_id": "tr-b",
                    "feedback_scores": [{"name": "verdict", "value": 2, "category_name": "high"}],
                },
            ],
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "id | data.question | verdict | passed" in out
    # The legend still owns up to the missing direction; the column does not
    # carry the marker, because a label has no sign to misread.
    assert "verdict (direction unknown)" not in out


# --- errored, not low (OPIK-8394) ------------------------------------------- #


def _crashed(experiment_id: str, trace: str) -> dict[str, Any]:
    """What a task or a judge that raised leaves on the joined row: a run,
    a trace, and no score at all. The row carries no error field — the
    compare join selects none (``ExperimentItemCompare``) — so this is the
    whole fingerprint."""
    return {"experiment_id": experiment_id, "trace_id": trace, "feedback_scores": []}


@pytest.mark.anyio
async def test_a_run_that_scored_nothing_beside_one_that_did_reads_errored() -> None:
    """The asymmetry is the signal: the same case, the same judges, and one
    experiment recorded nothing. That is not a low score and never a 0, and
    the run to open is the one that produced nothing."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [_run(A, trace="tr-a", scores={"correctness": 0.9}), _crashed(B, "tr-b")],
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    row = next(line for line in out.splitlines() if line.startswith("case-1"))
    assert "0.9 / errored" in row
    assert "unscored" not in row, "a crash is not the same as a judge that scored nothing"
    assert "Δ" not in row, "there is nothing to subtract from an error"
    assert "0.9 / errored |" in row and " 0 " not in row, "an error is never a zero"
    assert "tr-b (E2)" in row, "the errored run is the one to open"


@pytest.mark.anyio
async def test_a_case_no_run_scored_is_unscored_rather_than_errored() -> None:
    """With nothing to be asymmetric against, a run that recorded nothing is
    a case nobody scored — the judges may simply not have run on it."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(B, scores={"correctness": 1}),
            ],
        ),
        _case("case-2", {"question": "River?"}, [_crashed(A, "tr-a2"), _crashed(B, "tr-b2")]),
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    row = next(line for line in out.splitlines() if line.startswith("case-2"))
    assert "unscored / unscored" in row
    assert "errored" not in row


@pytest.mark.anyio
async def test_the_note_counts_scored_errored_and_unscored_and_they_add_up() -> None:
    """Three readings of a case, counted apart so that none of them lands in
    the low scores, and summing to the page so that none of them is double
    counted either."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(B, trace="tr-b", scores={"correctness": 0.4}),
            ],
        ),
        _case(
            "case-2",
            {"question": "River?"},
            [_run(A, trace="tr-a2", scores={"correctness": 0.8}), _crashed(B, "tr-b2")],
        ),
        _case("case-3", {"question": "Sea?"}, [_crashed(A, "tr-a3"), _crashed(B, "tr-b3")]),
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    note = next(line for line in out.splitlines() if line.startswith("On this page,"))
    counted = re.search(
        r"(\d+) of (\d+) cases fully scored, (\d+) errored .* and (\d+) unscored", note
    )
    assert counted is not None, note
    scored, total, errored, unscored = (int(n) for n in counted.groups())
    assert (scored, errored, unscored) == (1, 1, 1)
    assert scored + errored + unscored == total == 3
    assert "open its worst_trace for error_info" in note


@pytest.mark.anyio
async def test_a_count_of_zero_keeps_its_number_and_drops_its_explanation() -> None:
    """The three counts have to add up, so a zero stays in the sentence; there
    is nothing to explain about it, so it does not pay for the explanation."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(B, trace="tr-b", scores={"correctness": 0.4}),
            ],
        ),
        _case("case-2", {"question": "River?"}, [_crashed(A, "tr-a2"), _crashed(B, "tr-b2")]),
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    note = next(line for line in out.splitlines() if line.startswith("On this page,"))
    assert "1 of 2 cases fully scored, 0 errored and 1 unscored (no run" in note
    assert "open its worst_trace for error_info" not in note, "nothing errored to explain"


@pytest.mark.anyio
async def test_a_page_where_every_run_scored_says_nothing_about_errors() -> None:
    fake = _fake(_DEFAULT_CASE)
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "errored" not in out
    assert "unscored" not in out


# --- a cell is one cell, and so is a column name (OPIK-8394) --------------- #


@pytest.mark.anyio
async def test_a_case_data_key_with_a_line_break_and_a_pipe_stays_one_column() -> None:
    """The case columns are the dataset's own keys, so whatever the user
    named a field reaches the header: a newline split the header line in two
    and a bare pipe left the table with a column name no row had a cell for.
    The cells were escaped; the names they sit under were not."""
    fake = _fake(
        _case(
            "case-1",
            {"question | note\nsecond line": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(B, trace="tr-b", scores={"correctness": 0.4}),
            ],
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    header = next(line for line in out.splitlines() if line.startswith("id | "))
    row = next(line for line in out.splitlines() if line.startswith("case-1"))
    assert "data.question ¦ note second line" in header
    assert header.count(" | ") == row.count(" | "), "as many column names as the row has cells"


@pytest.mark.anyio
async def test_a_value_with_a_line_break_and_a_pipe_stays_one_cell() -> None:
    """A dataset item holds whatever the user put in it. A newline split the
    row in two and a bare pipe added a column to it."""
    fake = FakeOpikClient(
        dataset_items=_items({"question": "Line one\nLine two | with a pipe", "answer": "Paris"})
    )

    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)

    lines = out.splitlines()
    rows = [line for line in lines if line.startswith("i-1")]
    assert len(rows) == 1, "the row must stay on one line"
    assert rows[0].count(" | ") == 2, "id, data.answer, data.question"
    assert lines[2].count(" | ") == rows[0].count(" | "), "the header has the row's columns"
    assert "Line one Line two ¦ with a pipe" in rows[0]


@pytest.mark.anyio
async def test_a_data_key_with_a_line_break_and_a_pipe_stays_one_column() -> None:
    """The columns are the item's own keys, so the same two characters reach
    the header — where a newline split the header line in two and left the
    table with more column names than any row had cells."""
    fake = FakeOpikClient(
        dataset_items=_items({"question | note": "why", "answer\nline": "because"})
    )

    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)

    lines = out.splitlines()
    header = lines[2]
    assert header == "id | data.answer line | data.question ¦ note"
    assert lines[3].count(" | ") == header.count(" | "), "as many cells as column names"
    assert lines[3].startswith("i-1 | because | why")
