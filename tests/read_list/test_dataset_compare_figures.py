"""The comparison's honesty rules and its per-experiment figures.

OPIK-8394: a delta carries the arithmetic sign and the page says no direction
is recorded; a label is never averaged; a run that scored nothing is not a
missing run; a failed assertion is named; two authors of one score are both
shown. OPIK-8395: every page carries each experiment's figures over the runs
the filter matched, from one stats call per experiment.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from opik_mcp.opik_client import OpikServerError
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.oql import compile_filters

from .test_dataset_compare import _DEFAULT_CASE, A, B, _case, _experiment, _fake, _run, _summaries


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- direction, type and errors (OPIK-8394) --------------------------------- #


@pytest.mark.anyio
async def test_the_delta_is_signed_and_the_note_owns_up_to_the_missing_direction() -> None:
    """Opik's feedback definitions record no direction, so the sign is
    arithmetic — E2 minus E1 — and the note says a + on a lower-is-better
    metric is the regression. A drop is never dressed as a gain, because
    nothing is dressed as anything."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, scores={"correctness": 0.4, "hallucination": 0.9}),
                _run(B, scores={"correctness": 0.9, "hallucination": 0.4}),
            ],
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "0.4 / 0.9 Δ+0.5" in out
    assert "0.9 / 0.4 Δ-0.5" in out
    assert "Δ is E2 minus E1 (a + means E2 scored higher)" in out
    assert "No score definition records which direction is better" in out
    assert "on a lower-is-better metric a + is the regression" in out


@pytest.mark.anyio
async def test_an_equal_score_has_a_plain_zero_delta() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [_run(A, scores={"correctness": 0.7}), _run(B, scores={"correctness": 0.7})],
        )
    )
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "0.7 / 0.7 Δ0" in out


def _labelled(
    experiment_id: str, trace: str, name: str, value: float, label: str
) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "trace_id": trace,
        "feedback_scores": [{"name": name, "value": value, "category_name": label}],
    }


@pytest.mark.anyio
async def test_a_categorical_score_shows_its_label_and_gets_no_mean_or_delta() -> None:
    """A label is not a number. Two runs of one experiment show both labels,
    and nothing is averaged or subtracted."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _labelled(A, "tr-a", "verdict", 0, "low"),
                _labelled(A, "tr-a2", "verdict", 2, "high"),
                _labelled(B, "tr-b", "verdict", 2, "high"),
            ],
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    row = next(line for line in out.splitlines() if line.startswith("case-1"))
    assert "low,high / high" in row
    assert "Δ" not in row
    assert "1 /" not in row, "a categorical value is never its number"
    assert "/ 2" not in row, "a categorical value is never its number"


@pytest.mark.anyio
async def test_the_definition_restores_the_label_of_a_score_written_as_a_bare_number() -> None:
    """The SDK writes ``value=1`` and no label; only the definition knows that
    ``1`` is ``medium``. A number the definition does not list stays a number."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [_run(A, scores={"verdict": 1}), _run(B, scores={"verdict": 0.5})],
        ),
        feedback_definitions={
            "content": [
                {
                    "name": "verdict",
                    "type": "categorical",
                    "details": {"categories": {"low": 0, "medium": 1, "high": 2}},
                }
            ],
            "total": 1,
        },
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "medium / 0.5" in out
    assert "Δ" not in next(line for line in out.splitlines() if line.startswith("case-1"))
    assert fake.definition_calls == [{"size": 100}]


@pytest.mark.anyio
async def test_definitions_that_cannot_be_read_leave_every_score_a_number() -> None:
    fake = _fake(_DEFAULT_CASE, definitions_error=OpikServerError("definitions down"))
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "0.9 / 0.4 Δ-0.5" in out


@pytest.mark.anyio
async def test_a_run_that_recorded_nothing_reads_errored_not_as_a_missing_run() -> None:
    """A dash is "did not run this case". A run that exists and scored nothing
    beside one that scored the same case is a third thing — a task or judge
    that raised — and the note counts it apart from the low scores."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                {"experiment_id": B, "trace_id": "tr-b", "feedback_scores": []},
            ],
        ),
        _case(
            "case-2",
            {"question": "River?"},
            [
                _run(A, trace="tr-a2", scores={"correctness": 0.2}),
                _run(B, trace="tr-b2", scores={"correctness": 0.3}),
            ],
        ),
    )
    fake.experiment_records[A] = _experiment(A, "rerank-v1", method="dataset")
    fake.experiment_records[B] = _experiment(B, "rerank-v3", method="dataset")

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Capital? | 0.9 / errored" in out
    assert "Δ" not in next(line for line in out.splitlines() if line.startswith("case-1"))
    assert "On this page, 1 of 2 cases fully scored, 1 errored" in out
    assert "what a task or judge that raised looks like here" in out
    assert "open its worst_trace for error_info" in out
    assert "and 0 unscored" in out, "the counts partition the page even when one is empty"
    assert "not run by every experiment" not in out, "the run exists; it is not a missing run"
    # The errored run is the one to open: its total is the lowest there is.
    assert "0.9 / errored | tr-b (E2)" in out


@pytest.mark.anyio
async def test_the_note_counts_failed_and_errored_cases_apart() -> None:
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                _run(B, trace="tr-b", scores={"correctness": 0.4}, passed=False, reason="Lyon."),
            ],
            summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (0, 1)})},
        ),
        _case(
            "case-2",
            {"question": "River?"},
            [
                _run(A, trace="tr-a2", scores={"correctness": 0.8}),
                {
                    "experiment_id": B,
                    "trace_id": "tr-b2",
                    "feedback_scores": None,
                    "status": "passed",
                    "assertion_results": [],
                },
            ],
        ),
        _case(
            "case-3",
            {"question": "Sea?"},
            [
                _run(A, trace="tr-a3", scores={"correctness": 0.8}),
                _run(B, trace="tr-b3", scores={"correctness": 0.8}),
            ],
        ),
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    # A failed assertion is a different axis from how the case was scored:
    # case-1 failed one and is fully scored, case-2 errored, case-3 is clean.
    assert "On this page, 1 case failed an assertion; 2 of 3 cases fully scored, 1 errored" in out


@pytest.mark.anyio
async def test_a_page_with_nothing_failed_or_unscored_carries_no_tally() -> None:
    fake = _fake(_DEFAULT_CASE)
    fake.experiment_records[A] = _experiment(A, "rerank-v1", method="dataset")
    fake.experiment_records[B] = _experiment(B, "rerank-v3", method="dataset")
    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)
    assert "On this page," not in out


@pytest.mark.anyio
async def test_a_failed_assertion_is_named_with_its_reason() -> None:
    """A suite checks several assertions; a reason without the assertion says
    the case fell but not on what."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                _run(A, trace="tr-a", scores={"correctness": 0.9}),
                {
                    "experiment_id": B,
                    "trace_id": "tr-b",
                    "feedback_scores": [{"name": "correctness", "value": 0.4}],
                    "status": "failed",
                    "assertion_results": [
                        {"value": "answers in French", "passed": True, "reason": None},
                        {"value": "names the capital", "passed": False, "reason": "Names Lyon."},
                    ],
                },
            ],
            summaries={**_summaries(**{A: (1, 1)}), **_summaries(**{B: (0, 1)})},
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "| tr-b (E2) | names the capital: Names Lyon." in out
    assert "answers in French" not in out, "only the assertion that broke is named"


@pytest.mark.anyio
async def test_a_human_and_a_judge_score_of_one_name_are_both_shown() -> None:
    """The backend folds the authors of one score into ``value_by_author``. A
    judge's 0.9 and a reviewer's 0.2 are two opinions; their mean, 0.55, is
    nobody's, and the disagreement is the finding."""
    fake = _fake(
        _case(
            "case-1",
            {"question": "Capital?"},
            [
                {
                    "experiment_id": A,
                    "trace_id": "tr-a",
                    "feedback_scores": [
                        {
                            "name": "correctness",
                            "value": 0.9,
                            "source": "sdk",
                            "value_by_author": {
                                "judge": {"value": 0.9, "source": "sdk"},
                                "alice": {"value": 0.2, "source": "ui"},
                            },
                        }
                    ],
                },
                _run(B, trace="tr-b", scores={"correctness": 0.4}),
            ],
        )
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    row = next(line for line in out.splitlines() if line.startswith("case-1"))
    assert "0.9 sdk, 0.2 ui / 0.4" in row
    assert "Δ" not in row
    assert "0.55" not in row


# --- per-experiment figures (OPIK-8395) -------------------------------------- #


def _stats(
    runs: int, scores: dict[str, float], *, cost: float = 0.000012, p50: float = 1260.1
) -> dict[str, Any]:
    """The stats body as the endpoint serves it: names typed, percentiles keyed."""
    return {
        "stats": [
            {"name": "experiment_items_count", "value": runs, "type": "COUNT"},
            {"name": "trace_count", "value": runs, "type": "COUNT"},
            {"name": "total_estimated_cost", "value": cost, "type": "AVG"},
            {
                "name": "total_estimated_cost",
                "value": {"p50": cost, "p90": cost, "p99": cost},
                "type": "PERCENTAGE",
            },
            {
                "name": "duration",
                "value": {"p50": p50, "p90": p50 * 2, "p99": p50 * 3},
                "type": "PERCENTAGE",
            },
            *(
                {"name": f"feedback_scores.{name}", "value": mean, "type": "AVG"}
                for name, mean in scores.items()
            ),
            {"name": "usage.total_tokens", "value": 36.0, "type": "AVG"},
        ]
    }


@pytest.mark.anyio
async def test_every_comparison_carries_each_experiments_figures_under_the_count() -> None:
    fake = _fake(
        _DEFAULT_CASE,
        compared_stats={
            A: _stats(20, {"correctness": 0.82, "hallucination": 0.1}),
            B: _stats(20, {"correctness": 0.71, "hallucination": 0.12}, cost=0.000015, p50=1400),
        },
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    lines = out.splitlines()
    found = lines.index("Found 1 dataset_items (page 1, showing 1 of 1):")
    assert lines[found + 1] == (
        "E1: 20 runs; correctness 0.82, hallucination 0.1; avg cost 1.2e-05; p50 1260 ms"
    )
    assert lines[found + 2] == (
        "E2: 20 runs; correctness 0.71, hallucination 0.12; avg cost 1.5e-05; p50 1400 ms"
    )
    assert (
        "each experiment's figures over every run: runs, mean per score, mean cost, "
        "median duration" in out
    )
    assert "usage" not in out, "token means are not what the header is for"
    # One stats call per experiment, never one per row.
    assert [c["experiment_ids"] for c in fake.stats_calls] == [[A], [B]]
    assert all(c["filters"] is None for c in fake.stats_calls)


@pytest.mark.anyio
async def test_the_figures_follow_the_filter_so_a_threshold_count_is_one_call() -> None:
    """ "How many cases are below 0.5 in A and in B" is the header of one
    filtered call: the stats endpoint takes the page's own filter array."""
    sent = '[{"field":"feedback_scores","operator":"<","key":"correctness","value":"0.5"}]'
    fake = _fake(
        _DEFAULT_CASE,
        compared_stats={
            (A, sent): _stats(0, {}),
            (B, sent): _stats(7, {"correctness": 0.31}),
            A: _stats(20, {"correctness": 0.82}),
            B: _stats(20, {"correctness": 0.71}),
        },
    )

    out = await run_list(
        "dataset_item",
        experiment_ids=[A, B],
        filters="feedback_scores.correctness < 0.5",
        size=5,
        client=fake,
    )

    assert "E1: 0 runs match" in out
    assert "E2: 7 runs; correctness 0.31;" in out
    assert "figures over the runs matching the filter" in out
    assert "A different run count per experiment means the filter matched them differently" in out
    assert [c["filters"] for c in fake.stats_calls] == [sent, sent]


@pytest.mark.anyio
async def test_a_categorical_score_in_the_figures_is_counted_per_label_not_averaged() -> None:
    """``low`` is stored as 0 and ``high`` as 2; a mean of 1.3 says nothing.
    One count call per experiment per label, each pinning the score to that
    label's number on top of the page's own filter."""
    clauses = compile_filters("dataset_item", 'data.category = "billing"')
    base = json.dumps(clauses, separators=(",", ":"))

    def pinned(value: str) -> str:
        return json.dumps(
            [
                *clauses,
                {"field": "feedback_scores", "key": "verdict", "operator": "=", "value": value},
            ],
            separators=(",", ":"),
        )

    fake = _fake(
        _DEFAULT_CASE,
        feedback_definitions={
            "content": [
                {
                    "name": "verdict",
                    "type": "categorical",
                    "details": {"categories": {"low": 0, "high": 2}},
                }
            ],
            "total": 1,
        },
        compared_stats={
            (A, base): _stats(20, {"verdict": 1.3, "correctness": 0.8}),
            (B, base): _stats(20, {"verdict": 0.4, "correctness": 0.7}),
            (A, pinned("0")): _stats(7, {}),
            (A, pinned("2")): _stats(13, {}),
            (B, pinned("0")): _stats(16, {}),
            (B, pinned("2")): _stats(4, {}),
        },
    )

    out = await run_list(
        "dataset_item", experiment_ids=[A, B], filters='data.category = "billing"', client=fake
    )

    assert "E1: 20 runs; correctness 0.8, verdict low 7, high 13;" in out
    assert "E2: 20 runs; correctness 0.7, verdict low 16, high 4;" in out
    assert "1.3" not in out
    assert "verdict 0.4" not in out
    assert len(fake.stats_calls) == 2 + 4


@pytest.mark.anyio
async def test_too_many_labels_to_count_are_named_with_the_call_that_counts_one() -> None:
    fake = _fake(
        _DEFAULT_CASE,
        feedback_definitions={
            "content": [
                {
                    "name": "rubric",
                    "type": "categorical",
                    "details": {"categories": {f"level-{n}": n for n in range(7)}},
                }
            ],
            "total": 1,
        },
        compared_stats={A: _stats(20, {"rubric": 3.1}), B: _stats(20, {"rubric": 2.9})},
    )

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "E1: 20 runs; rubric categorical, not counted;" in out
    assert "rubric is categorical and not counted per label here (more than 12 calls)" in out
    assert "filters='feedback_scores.rubric = <value>'" in out
    assert len(fake.stats_calls) == 2, "fourteen count calls were not made"


@pytest.mark.anyio
async def test_figures_that_cannot_be_fetched_leave_the_page_standing() -> None:
    fake = _fake(_DEFAULT_CASE, stats_error=OpikServerError("stats down"))

    out = await run_list("dataset_item", experiment_ids=[A, B], client=fake)

    assert "case-1 | Paris | Capital of France? | 0.9 / 0.4 Δ-0.5" in out
    assert "E1: figures unavailable (stats down)" in out
    assert "E2: figures unavailable (stats down)" in out


@pytest.mark.anyio
async def test_an_empty_page_still_carries_the_figures() -> None:
    """A filter that matches no case on this page can still match runs; and a
    count of zero per experiment is itself the answer to "how many"."""
    fake = _fake(
        total=0,
        compared_stats={A: _stats(0, {}), B: _stats(3, {"correctness": 0.2})},
    )

    out = await run_list(
        "dataset_item",
        experiment_ids=[A, B],
        filters="feedback_scores.correctness < 0.5",
        client=fake,
    )

    assert "E1: 0 runs match" in out
    assert "E2: 3 runs; correctness 0.2;" in out
