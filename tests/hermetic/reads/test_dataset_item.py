"""read('dataset_item') and list('dataset_item'): finding a case, and comparing experiments.

The joined comparison endpoint is picky: the experiment ids ride in a query
param as one JSON array, and a filter on the runs comes back with the other
runs stripped off the row. The items route takes a ``filters`` array, and a
case is addressed without its dataset. So the request shape is most of what
can break here, and each test reads it back off the stub.
"""

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
)
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_compare import CompareSuite, ExperimentSpec
from tests.hermetic.stub_records import (
    CASE_ID,
    CASE_TRACE_ID,
    EXPERIMENT_A,
    EXPERIMENT_B,
    EXPERIMENT_OTHER_SUITE,
    SUITE_ID,
)

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

_JOINED = f"/v1/private/datasets/{SUITE_ID}/items/experiments/items"
#: The ids as the backend reads them: one JSON array, not comma-joined.
_BOTH = json.dumps([EXPERIMENT_A, EXPERIMENT_B], separators=(",", ":"))

CALLS: list[Call] = [
    ("read", {"id": CASE_ID}),
    ("list", {"dataset_id": SUITE_ID}),
    ("list", {"experiment_ids": [EXPERIMENT_A, EXPERIMENT_B]}),
]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "dataset_item", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "dataset_item", CALLS)


# --- finding one case ------------------------------------------------------- #


async def test_a_case_key_reaches_the_backend_as_the_maps_wire_form(
    backend: StubBackend,
) -> None:
    """The acceptance case: one filter, on a key the user named, arriving as
    the MAP clause opik-backend deserializes — field ``data`` with the key
    beside it, not spliced into the field name."""
    backend.suite = CompareSuite(case_count=2_000)

    async with stdio_session(backend) as session:
        out = await call(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            filters='data.question contains "install" AND tags contains "regression"',
            size=5,
        )

    assert backend.one("/items").filters() == [
        {"field": "data", "key": "question", "operator": "contains", "value": "install"},
        {"field": "tags", "key": "", "operator": "contains", "value": "regression"},
    ]
    assert out.splitlines()[0].endswith(
        ' tok | filters: data.question contains "install" AND tags contains "regression"]'
    )
    assert "Found 2000 dataset_items (page 1, showing 5 of 2000)" in out


async def test_the_source_trace_is_one_call_and_a_comparison_is_none(
    backend: StubBackend,
) -> None:
    """``trace_id`` is how a case is found from the conversation it was made
    from. A comparison on a map key is refused before anything is sent: the
    backend answers 400 for the pair, and there is nothing to learn from it."""
    async with stdio_session(backend) as session:
        await call(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            filters=f'trace_id = "{CASE_TRACE_ID}"',
        )
        refusal = await refuse(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            filters="data.score > 0.5",
        )
        sort_refusal = await refuse(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            sort="created_at desc",
        )

    assert backend.one("/items").filters() == [
        {"field": "trace_id", "key": "", "operator": "=", "value": CASE_TRACE_ID}
    ]
    assert "not valid for 'data' (map)" in refusal
    assert "sort is not supported" in sort_refusal


async def test_read_returns_the_case_the_table_had_to_cut(backend: StubBackend) -> None:
    """The listing cuts every cell to fit a row and says so; the read of the
    id it printed is the same case with nothing taken out."""
    async with stdio_session(backend) as session:
        table = await call(
            session, "list", entity_type="dataset_item", dataset_id=SUITE_ID, size=25
        )
        record = await call(session, "read", entity_type="dataset_item", id=CASE_ID)

    assert "values cut at" in table
    assert "read('dataset_item', id) is the value whole" in table
    notes = json.loads(record.split("\n", 1)[1])["data"]["notes"]
    assert notes.startswith("Case 3: ")
    assert len(notes) > 1_000
    assert notes not in table


# --- comparing experiments case by case -------------------------------------- #


async def test_comparing_two_experiments_lines_their_cases_up(backend: StubBackend) -> None:
    backend.suite = CompareSuite(case_count=8)

    async with stdio_session(backend) as session:
        answer = await call(
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

    assert answer.splitlines()[0].endswith(
        " tok | compare: "
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
    assert "(E2)" in regressed
    assert "names Lyon, not Paris." in regressed
    assert "Use page=2 for next 4 results." in answer
    # ``input`` is the suite's own case, echoed back by the run.
    assert "runs' output keys: answer, reasoning" in answer
    assert "case data keys: expected_answer, question" in answer


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

    async with stdio_session(backend) as session:
        answer = await call(
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

    async with stdio_session(backend) as session:
        wide = await call(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            size=4,
        )
        narrow = await call(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_B],
            size=4,
            fields=["data.question", "feedback_scores.correctness"],
        )
        refusal = await refuse(
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
        async with stdio_session(backend) as session:
            answers[case_count] = await call(
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


async def test_experiments_from_two_datasets_are_refused_before_anything_is_joined(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        refusal = await refuse(
            session,
            "list",
            entity_type="dataset_item",
            experiment_ids=[EXPERIMENT_A, EXPERIMENT_OTHER_SUITE],
        )

    assert "support-qa" in refusal
    assert "billing-qa" in refusal
    assert not backend.called("items/experiments/items")


async def test_a_filter_on_the_runs_comes_back_with_every_run_on_the_row(
    backend: StubBackend,
) -> None:
    """The backend answers a run-level filter with the other runs stripped off.
    Reading that page as it arrives cannot tell a regression from a case that
    was always bad, so each matched case is fetched again without the clause."""
    backend.suite = CompareSuite(case_count=8, strip_to_experiment=EXPERIMENT_B)

    async with stdio_session(backend) as session:
        answer = await call(
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


async def test_a_filtered_comparison_costs_the_same_on_a_hundred_thousand_cases(
    backend: StubBackend,
) -> None:
    answers: dict[int, str] = {}
    calls: dict[int, list[str]] = {}
    for case_count in (20, 100_000):
        backend.suite = CompareSuite(case_count=case_count, strip_to_experiment=EXPERIMENT_B)
        backend.requests.clear()
        async with stdio_session(backend) as session:
            answers[case_count] = await call(
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
