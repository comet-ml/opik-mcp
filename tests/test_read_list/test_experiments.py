"""``list('experiment')`` — the row shows the run, and says what it left out.

The listing used to show five columns and drop everything that says whether
a comparison between two runs is even valid: status, prompt version, dataset
version, cost, duration, how many assertion runs passed. All of it arrives in
the same response. An engineer comparing six runs paid six extra reads to
recover fields the page already had.

Whole classes of experiment are null for whole groups of these fields — an
optimizer workspace has no assertion counts and no linked prompts at all — so
the columns are chosen from the page. These tests pin the spine that is always
there, the conditionals that appear only when some row has one, and the note
that names every omission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from opik_mcp.opik_client import OpikServerError
from opik_mcp.read_list.entities.experiment import (
    HANDLER,
    derive_columns,
    project_experiments,
)
from opik_mcp.read_list.list_tool import run_list

from .test_list_tool import FakeOpikClient

RUN = "019fb348-cf24-78a5-bd6f-9b22527c02b6"


def _project(*items: dict[str, Any]) -> Any:
    """Derive then project, the order the list tool uses. The projection sees
    the row the table will render, not the row the backend sent."""
    return project_experiments([derive_columns(item) for item in items])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _experiment(name: str, **extra: Any) -> dict[str, Any]:
    """A regular experiment as the backend serves it in a listing."""
    return {
        "id": f"exp-{name}",
        "name": name,
        "dataset_name": "support-qa",
        "created_at": "2026-07-30T13:50:05.836520294Z",
        "type": "regular",
        "status": "completed",
        "duration": {"p50": 1260.107, "p90": 1447.311, "p99": 1489.4319},
        "total_estimated_cost": 1.34e-05,
        "feedback_scores": [{"name": "accuracy", "value": 0.82}],
        "dataset_version_summary": {"version_name": "v1", "version_hash": "04cfe397"},
        **extra,
    }


def _page(*items: dict[str, Any]) -> dict[str, Any]:
    return {"content": list(items), "total": len(items)}


def _header_index(out: str) -> int:
    """Where the column header sits.

    Not a fixed offset: a call with filters or a sort prints an applied line
    above the count, which moved every row down by one and cost a test its
    meaning until it was noticed.
    """
    for index, line in enumerate(out.splitlines()):
        if line.startswith("id | name"):
            return index
    raise AssertionError(f"no column header in:\n{out}")


def _columns(out: str) -> list[str]:
    """The header row of the rendered table."""
    return [c.strip() for c in out.splitlines()[_header_index(out)].split("|")]


def _row(out: str, n: int = 0) -> str:
    """One data row, counting from the first under the header."""
    return out.splitlines()[_header_index(out) + 1 + n]


def _cells(out: str, n: int = 0) -> list[str]:
    return [c.strip() for c in _row(out, n).split("|")]


# --- the projection ------------------------------------------------------- #


def test_the_spine_is_there_even_when_its_values_are_not() -> None:
    """Two pages that disagree about their first columns cannot be read side
    by side, so the spine does not depend on the page."""
    projection = _project({"id": "e-1", "name": "bare"})
    assert projection.columns[:6] == (
        "type",
        "status",
        "dataset_name",
        "created_at",
        "trace_count",
        "feedback_scores",
    )


def test_the_sample_size_sits_beside_the_score_it_qualifies() -> None:
    """A mean with no count invites a decision the data cannot support.

    Driving the tool: a trial ranked first at 0.634 turned out to be a mean
    over three cases, and it lost one of them. The page gave nothing to doubt
    it with. trace_count is on the record, so the count reads immediately to
    the left of the score it qualifies.
    """
    columns = _project(_experiment("nightly")).columns
    assert columns.index("trace_count") == columns.index("feedback_scores") - 1


def test_a_column_no_row_can_fill_is_left_out() -> None:
    """Measured live: 32 of 32 experiments in an optimizer workspace had no
    assertion counts and no linked prompt. Printing both would be two columns
    of nothing on every row."""
    projection = _project(_experiment("nightly"))
    assert "assertion_runs" not in projection.columns
    assert "prompt_version" not in projection.columns


def test_a_column_one_row_can_fill_is_kept_for_the_whole_page() -> None:
    projection = _project(
        _experiment("nightly"),
        _experiment("suite-run", passed_count=7, total_count=10),
    )
    assert "assertion_runs" in projection.columns


def test_conditional_columns_keep_their_order_whichever_subset_shows() -> None:
    """A stable left-to-right reading survives the set changing between
    pages; a set that reorders itself does not."""
    full = _project(
        _experiment(
            "everything",
            passed_count=7,
            total_count=10,
            prompt_versions=[{"version_number": "v3"}],
            optimization_id=RUN,
        )
    ).columns
    partial = _project(_experiment("some", optimization_id=RUN)).columns
    assert [c for c in full if c in partial] == list(partial)


def test_the_note_names_what_was_left_out_and_why() -> None:
    note = _project(_experiment("nightly")).note
    assert note is not None
    assert "assertion_runs" in note and "prompt_version" in note
    assert "no row" in note


def test_the_note_carries_the_filter_vocabulary_even_with_nothing_omitted() -> None:
    """The columns teach what can be sorted; this line is where every field a
    caller may filter on gets named, column or not."""
    note = _project(
        _experiment(
            "everything",
            passed_count=7,
            total_count=10,
            prompt_versions=[{"version_number": "v3"}],
            optimization_id=RUN,
        )
    ).note
    assert note is not None
    # The three that are query parameters rather than OQL fields...
    assert "type" in note and "optimization_id" in note and "experiment_ids" in note
    # ...and the ones a column never advertised: dataset_id is filterable
    # while the column is dataset_name, and these are not columns at all.
    for hidden in ("dataset_id", "metadata", "project_id", "prompt_ids", "tags"):
        assert hidden in note, f"{hidden} is filterable and went unnamed"
    assert 'schema("list.experiment")' in note, "and the operators are one call away"


# --- the cells ------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_row_carries_what_a_comparison_needs() -> None:
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", client=fake)
    row = _row(out)
    assert "regular" in row
    assert "completed" in row
    assert "support-qa" in row
    assert "accuracy=0.82" in row
    assert "v1" in row


@pytest.mark.anyio
async def test_assertion_runs_read_as_passed_over_total() -> None:
    fake = FakeOpikClient(
        experiments=_page(_experiment("suite-run", passed_count=7, total_count=10))
    )
    out = await run_list("experiment", client=fake)
    assert "7/10" in _row(out)


@pytest.mark.anyio
async def test_an_experiment_without_assertions_shows_nothing_not_zero() -> None:
    """The counts are null for a run with no assertions. A zero there would
    read as "nothing passed", which is a different and much worse answer."""
    fake = FakeOpikClient(
        experiments=_page(
            _experiment("suite-run", passed_count=7, total_count=10),
            _experiment("plain"),
        )
    )
    out = await run_list("experiment", client=fake)
    plain = _row(out, 1)
    assert "0/0" not in plain
    assert "" in [c.strip() for c in plain.split("|")]


@pytest.mark.anyio
async def test_duration_shows_the_typical_case_and_the_tail_in_milliseconds() -> None:
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", client=fake)
    columns = _columns(out)
    assert "duration.p50_ms" in columns
    assert "duration.p90_ms" in columns
    assert not any("p99" in c for c in columns)
    row = _cells(out)
    assert row[columns.index("duration.p50_ms")] == "1260"
    assert row[columns.index("duration.p90_ms")] == "1447"


@pytest.mark.anyio
async def test_sorting_by_the_tail_percentile_brings_its_column_with_it() -> None:
    """p99 is not a column of its own — p50 and p90 answer "typical" and
    "tail" and a third costs width for a question nobody asked. But
    ``duration.*`` is sortable, so a caller can ask for it, and the column
    the sort adds must carry its unit like the other two."""
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", sort="duration.p99 desc", client=fake)
    columns = _columns(out)
    assert "duration.p99_ms" in columns
    row = _cells(out)
    assert row[columns.index("duration.p99_ms")] == "1489"


@pytest.mark.anyio
async def test_cost_is_rounded_to_the_digits_that_mean_anything() -> None:
    """Seen live: a run costing 5.099999e-06 rendered as ``0.000005099999``,
    which is eleven digits of float noise in a column an engineer is scanning
    to compare spend. Six significant digits is already far past what a
    fraction of a cent can mean."""
    fake = FakeOpikClient(
        experiments=_page(_experiment("cheap", total_estimated_cost=5.099999e-06))
    )
    out = await run_list("experiment", client=fake)
    assert "0.0000051" in _row(out)
    assert "0.000005099999" not in out


@pytest.mark.anyio
async def test_a_multi_prompt_experiment_lists_every_version_it_ran() -> None:
    fake = FakeOpikClient(
        experiments=_page(
            _experiment(
                "two-prompts",
                prompt_versions=[{"version_number": "v3"}, {"version_number": "v1"}],
            )
        )
    )
    out = await run_list("experiment", client=fake)
    assert "v3,v1" in _row(out)


@pytest.mark.anyio
async def test_a_version_with_no_number_falls_back_to_its_commit() -> None:
    """Masks carry a commit and no sequential number; an empty cell would
    read as "no prompt" rather than "a prompt we cannot number"."""
    fake = FakeOpikClient(
        experiments=_page(
            _experiment("masked", prompt_versions=[{"version_number": None, "commit": "a1b2c3d4"}])
        )
    )
    out = await run_list("experiment", client=fake)
    assert "a1b2c3d4" in _row(out)


@pytest.mark.anyio
async def test_the_optimization_run_is_on_the_row_so_trials_group_without_a_read() -> None:
    """Ranking one optimization's trials needs the run each trial belongs to.
    Reading a trial to discover it is the round trip this feature removes.
    The column shows when the page was not filtered to one run — once it is,
    the header names the run and the column would repeat it on every row."""
    fake = FakeOpikClient(
        experiments=_page(_experiment("trial-7", type="trial", optimization_id=RUN))
    )
    out = await run_list("experiment", client=fake)
    assert "optimization_id" in _columns(out)
    assert RUN in _row(out)


@pytest.mark.anyio
async def test_an_ordinary_page_carries_no_optimization_column() -> None:
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", client=fake)
    assert "optimization_id" not in _columns(out)


# --- the empty page --------------------------------------------------------- #
#
# Both holes here were found by driving the built server, not by reading it.
# A page filtered to a type nobody has answered "No experiments found." in a
# workspace holding 32 — which an agent relays as "you have no experiments".
# And the filter vocabulary is advertised in the projection note, which only a
# non-empty page prints, so the one moment the caller needs the valid values
# is the one moment nothing names them.


@dataclass
class PickyClient(FakeOpikClient):
    """A backend where the filter matches nothing but the workspace is full."""

    stock: int = 32
    side_error: Exception | None = None
    past_the_end: bool = False
    """Serve an empty slice of a listing that does match — what the backend
    returns for a page beyond the last one: no rows, but a total."""

    async def list_experiments(self, **kw: Any) -> dict[str, Any]:
        self.last_kwargs = kw
        self.experiment_calls.append(kw)
        narrowed = any(k in kw for k in ("filters", "types", "optimization_id", "name"))
        if narrowed:
            return {"content": [], "total": 0}
        if self.past_the_end and kw.get("page", 1) > 1:
            return {"content": [], "total": self.stock}
        if self.side_error is not None:
            raise self.side_error
        return {"content": [_experiment("nightly")], "total": self.stock}

    @property
    def side_calls(self) -> int:
        """Lookups the note made on its own, beyond the listing itself."""
        return len(self.experiment_calls) - 1


@pytest.mark.anyio
async def test_an_empty_page_says_the_workspace_is_not_empty() -> None:
    out = await run_list("experiment", filters='type = "regular"', client=PickyClient())
    assert "32 experiments" in out
    assert "none match" in out


@pytest.mark.anyio
async def test_an_empty_page_names_the_values_the_filter_accepts() -> None:
    """The projection note cannot carry this — there are no rows to hang it
    under — so the recovery path has to live here or nowhere."""
    out = await run_list("experiment", filters='type = "regular"', client=PickyClient())
    assert "regular, trial, mini-batch, mutation" in out
    assert "optimization_id takes one id" in out
    # Seen live on the installed build: the set-valued field was described as
    # taking one id, the opposite of what it is for.
    assert "experiment_ids takes a set of ids" in out


@pytest.mark.anyio
async def test_a_genuinely_empty_workspace_does_not_blame_the_filter() -> None:
    out = await run_list("experiment", filters='type = "regular"', client=PickyClient(stock=0))
    assert out.endswith("No experiments found."), (
        "the plain message already says the workspace is empty; a note repeating it "
        f"in other words is one sentence too many. Got: {out!r}"
    )
    assert "none match" not in out


@pytest.mark.anyio
async def test_a_page_past_the_end_is_not_a_query_that_matched_nothing() -> None:
    """The first version of this note re-created the bug it was written to
    fix, one case over. An empty page is not always an empty result: paging
    past the last page returns no rows and a total, and claiming "none match"
    there is the same false answer in a different costume — they do match,
    they are on page one.
    """
    client = PickyClient(past_the_end=True)
    out = await run_list("experiment", page=3, client=client)
    assert "none match" not in out
    assert "past the end" in out
    assert client.side_calls == 0, "the page's own total already settles it"


@pytest.mark.anyio
async def test_a_name_search_is_not_advised_about_filter_fields_it_did_not_use() -> None:
    """The vocabulary sentence answers "what may I filter by". A caller who
    searched by name did not ask that, and does not need a filter lecture."""
    out = await run_list("experiment", name="zzz-nothing", client=PickyClient())
    assert "32 experiments" in out, "the count is useful either way"
    assert "type accepts" not in out


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boom",
    [OpikServerError("boom"), TypeError("boom"), RuntimeError("boom")],
    ids=["backend", "shape", "anything"],
)
async def test_a_failed_side_lookup_still_answers_the_list(boom: Exception) -> None:
    """The note decorates an answer the caller already has. A hook that
    cannot run must leave the page alone, never turn it into an error — and
    "cannot run" is not a list of exception types anyone can enumerate: a
    body that is not the shape we expect fails just as hard as a 500."""
    client = PickyClient(side_error=boom)
    out = await run_list("experiment", filters='type = "regular"', client=client)
    assert "No experiments found." in out
    assert "boom" not in out


@pytest.mark.anyio
async def test_a_full_page_pays_for_no_second_note() -> None:
    """The hint is in the projection note there; a page note as well would
    print the same advice twice."""
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", client=fake)
    assert "dataset_id" in out and 'schema("list.experiment")' in out, (
        "the projection note still carries the hint"
    )
    assert "type accepts" not in out, "and the page note stayed silent"
    assert "none match" not in out


@pytest.mark.anyio
async def test_the_workspace_count_costs_one_call_and_only_when_empty() -> None:
    client = PickyClient()
    await run_list("experiment", filters='type = "regular"', client=client)
    assert client.side_calls == 1

    quiet = PickyClient()
    await run_list("experiment", client=quiet)
    assert quiet.side_calls == 0, "a page with rows asks nothing extra"


# --- a ranking over too few cases ------------------------------------------- #
#
# Driving the tool: sorted by score, a trial stood first at 0.634. It was a
# mean over three cases and had lost one of them. trace_count beside the
# score lets a careful reader notice; this line is for the reader about to
# not notice.


def _ranked(*runs: tuple[str, float, int]) -> FakeOpikClient:
    """Rows already in score order, the way the backend returns a sorted page."""
    rows = [
        _experiment(name, trace_count=count, feedback_scores=[{"name": "accuracy", "value": v}])
        for name, v, count in runs
    ]
    return FakeOpikClient(experiments=_page(*rows))


@pytest.mark.anyio
async def test_a_ranking_over_a_thin_sample_is_flagged_with_the_gap_and_the_counts() -> None:
    fake = _ranked(("winner", 0.634, 3), ("runner-up", 0.575, 3), ("third", 0.48, 3))
    out = await run_list("experiment", sort="feedback_scores.accuracy desc", client=fake)
    assert "Ranked by feedback_scores.accuracy" in out
    assert "differ by 0.059 over 3 and 3 cases" in out
    assert "list('dataset_item'" in out, "and it points at the call that would settle it"


@pytest.mark.anyio
async def test_a_sort_the_backend_dropped_leaves_no_ranking_to_caveat() -> None:
    """The backend blanks ``sortable_by`` when it dropped the sort for a large
    workspace. The header says the page is unsorted; the caveat once fired
    anyway and told the caller the first two rows were ranked, so the answer
    contradicted itself (found in review on PR #192)."""
    fake = _ranked(("winner", 0.634, 3), ("runner-up", 0.575, 3))
    fake.experiments["sortable_by"] = []
    out = await run_list("experiment", sort="feedback_scores.accuracy desc", client=fake)
    assert "page is unsorted" in out.splitlines()[0]
    assert "Ranked by" not in out


@pytest.mark.anyio
async def test_a_ranking_over_enough_cases_carries_no_caveat() -> None:
    fake = _ranked(("winner", 0.634, 40), ("runner-up", 0.575, 25))
    out = await run_list("experiment", sort="feedback_scores.accuracy desc", client=fake)
    assert "does not settle" not in out


@pytest.mark.anyio
async def test_one_thin_run_among_the_top_two_is_enough_to_flag() -> None:
    fake = _ranked(("winner", 0.634, 3), ("runner-up", 0.575, 40))
    out = await run_list("experiment", sort="feedback_scores.accuracy desc", client=fake)
    assert "over 3 and 40 cases" in out


@pytest.mark.anyio
async def test_a_page_not_sorted_by_a_score_is_not_a_ranking() -> None:
    """Sorted by date, the order says nothing about who won, so there is no
    winner to caution against naming."""
    fake = _ranked(("a", 0.634, 3), ("b", 0.575, 3))
    by_date = await run_list("experiment", sort="created_at desc", client=fake)
    unsorted = await run_list("experiment", client=fake)
    assert "does not settle" not in by_date
    assert "does not settle" not in unsorted


@pytest.mark.anyio
async def test_a_single_row_is_not_a_ranking_either() -> None:
    fake = _ranked(("only", 0.634, 3))
    out = await run_list("experiment", sort="feedback_scores.accuracy desc", client=fake)
    assert "does not settle" not in out


# --- what the headers promise ---------------------------------------------- #


#: Columns the backend has no filter or sort field for. They are facts about
#: the run that only the row can tell you — an experiment's status is not
#: queryable on any endpoint — so they cannot teach a word, but they must not
#: teach a *wrong* one either: nothing here may be a rename of a field that
#: does exist.
_DISPLAY_ONLY = {
    "status",
    "dataset_name",
    "assertion_runs",
    "prompt_version",
    "dataset_version",
    # Not a field of the record at all: the address of the compare view this
    # run lives on, built per row because each row names its own project and
    # dataset. Nothing to sort or filter by, and nothing it could teach a
    # wrong spelling of.
    "url",
}


def test_no_column_renames_a_field_the_vocabulary_already_names() -> None:
    """The headers are how an agent learns what it may sort and filter by
    without reading the schema. A column called ``cost`` when the field is
    ``total_estimated_cost`` teaches a word that does not work — so every
    queryable column uses the vocabulary's own spelling, and the rest are
    declared above as having no spelling to match."""
    from opik_mcp.read_list.oql import FILTERABLE_FIELDS
    from opik_mcp.read_list.sorting import SORTABLE_FIELDS

    known = {f.removesuffix(".*") for f in SORTABLE_FIELDS["experiment"]}
    known |= set(FILTERABLE_FIELDS["experiment"])
    for column in _project(
        _experiment(
            "everything",
            passed_count=7,
            total_count=10,
            prompt_versions=[{"version_number": "v3"}],
            optimization_id=RUN,
        )
    ).columns:
        assert column.partition(".")[0] in known or column in _DISPLAY_ONLY, column


def test_the_handler_chooses_its_columns_from_the_page() -> None:
    assert HANDLER.list_projection_fn is project_experiments
