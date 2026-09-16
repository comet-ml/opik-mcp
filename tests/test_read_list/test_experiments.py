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

from typing import Any

import pytest

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


def _columns(out: str) -> list[str]:
    """The header row of the rendered table."""
    return [c.strip() for c in out.splitlines()[2].split("|")]


# --- the projection ------------------------------------------------------- #


def test_the_spine_is_there_even_when_its_values_are_not() -> None:
    """Two pages that disagree about their first columns cannot be read side
    by side, so the spine does not depend on the page."""
    projection = _project({"id": "e-1", "name": "bare"})
    assert projection.columns[:5] == (
        "type",
        "status",
        "dataset_name",
        "created_at",
        "feedback_scores",
    )


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
    """The columns teach what can be sorted; this line is where the two
    fields that are not columns on an ordinary page get named."""
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
    assert "type" in note and "optimization_id" in note


# --- the cells ------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_row_carries_what_a_comparison_needs() -> None:
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", client=fake)
    row = out.splitlines()[3]
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
    assert "7/10" in out.splitlines()[3]


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
    plain = out.splitlines()[4]
    assert "0/0" not in plain
    cells = [c.strip() for c in plain.split("|")]
    assert "" in cells


@pytest.mark.anyio
async def test_duration_shows_the_typical_case_and_the_tail_in_milliseconds() -> None:
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", client=fake)
    columns = _columns(out)
    assert "duration.p50_ms" in columns
    assert "duration.p90_ms" in columns
    assert not any("p99" in c for c in columns)
    row = [c.strip() for c in out.splitlines()[3].split("|")]
    assert row[columns.index("duration.p50_ms")] == "1260"
    assert row[columns.index("duration.p90_ms")] == "1447"


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
    assert "0.0000051" in out.splitlines()[3]
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
    assert "v3,v1" in out.splitlines()[3]


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
    assert "a1b2c3d4" in out.splitlines()[3]


@pytest.mark.anyio
async def test_the_optimization_run_is_on_the_row_so_trials_group_without_a_read() -> None:
    """Ranking one optimization's trials needs the run each trial belongs to.
    Reading a trial to discover it is the round trip this feature removes."""
    fake = FakeOpikClient(
        experiments=_page(_experiment("trial-7", type="trial", optimization_id=RUN))
    )
    out = await run_list("experiment", client=fake)
    assert "optimization_id" in _columns(out)
    assert RUN in out.splitlines()[3]


@pytest.mark.anyio
async def test_an_ordinary_page_carries_no_optimization_column() -> None:
    fake = FakeOpikClient(experiments=_page(_experiment("nightly")))
    out = await run_list("experiment", client=fake)
    assert "optimization_id" not in _columns(out)


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
