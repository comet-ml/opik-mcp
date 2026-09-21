"""``fields=[…]`` — the caller names what ``read`` and ``list`` return.

The thing these pin is not that projection works but that it can never be
mistaken for the whole: a page says which fields its records carry, a
projected answer says it was projected and what it left out, and a row keeps
the id that opens the next level whether or not anyone asked for it. A silent
cut is the one failure this feature could introduce, and the one every test
here is aimed at.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list import projection
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.projection import FieldsError
from opik_mcp.read_list.read_tool import run_read

from .test_dataset_compare import DATASET, A, B, _case, _fake, _run
from .test_list_tool import FakeOpikClient
from .test_read_tool import FakeOpikClient as FakeReadClient

TRACE = "019f8d97-c83c-7597-b40a-bd2e0e1ad500"
PROJECT = "019f8d97-c83c-7597-b40a-bd2e0e1ad501"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- the helpers ---------------------------------------------------------- #


def test_normalise_trims_drops_blanks_and_keeps_first_mention() -> None:
    assert projection.normalise([" a ", "b", "a", "", "  "]) == ("a", "b")
    assert projection.normalise([]) is None
    assert projection.normalise(None) is None
    assert projection.normalise(["  "]) is None


def test_row_fields_names_flat_keys_dict_keys_and_named_entries() -> None:
    """The names offered are exactly the names ``columns.resolve`` finds: a
    flat key, one level into a dict, and a ``[{name, value}]`` list by name."""
    rows: list[dict[str, Any]] = [
        {
            "id": "t-1",
            "data": {"question": "q", "answer": "a"},
            "feedback_scores": [{"name": "helpfulness", "value": 0.9}],
        },
        {"id": "t-2", "duration": 12.0, "data": {"question": "q"}},
    ]
    assert projection.row_fields(rows) == (
        # The container is nameable too — ``data`` is the whole map — so
        # nothing the rows carry is offered only in pieces.
        "data",
        "data.answer",
        "data.question",
        "duration",
        "feedback_scores",
        "feedback_scores.helpfulness",
        "id",
    )


def test_row_fields_names_every_key_and_offered_is_the_short_menu() -> None:
    """The set a page *accepts* is complete; the line it *prints* is a subset
    that counts what it left out.

    They were one capped set, and the bug that made was the worst kind this
    module can have: a key that resolves on the row, refused with a message
    saying the records do not carry it.
    """
    wide = {f"k{i:03d}": i for i in range(projection.MAX_NESTED_NAMES + 5)}
    names = projection.row_fields([{"id": "t-1", "metadata": wide}])
    assert "metadata" in names
    assert len([n for n in names if n.startswith("metadata.")]) == len(wide)

    shown = projection.offered(names)
    assert len([n for n in shown if n.startswith("metadata.")]) == projection.MAX_NESTED_NAMES
    assert shown[-1] == "+5 more"


def test_offered_never_lets_one_container_crowd_out_the_flat_keys() -> None:
    """The per-container cap exists so ``id`` and ``start_time`` survive a
    hundred-key metadata map. Without it the menu is all one container."""
    wide = {f"k{i:03d}": i for i in range(200)}
    names = projection.row_fields([{"id": "t-1", "start_time": "x", "metadata": wide}])
    shown = projection.offered(names)
    assert "id" in shown and "start_time" in shown and "metadata" in shown


def test_covers_reads_a_container_as_keeping_its_children() -> None:
    assert projection.covers("trace", "trace.output")
    assert projection.covers("trace", "trace")
    assert not projection.covers("trace.output", "trace.input")
    # Not a prefix match on the raw string: ``trace`` must not cover ``traces``.
    assert not projection.covers("trace", "traceback")


def test_record_paths_stops_at_a_list() -> None:
    """Arrays are kept whole, so a path never reaches inside one."""
    paths = projection.record_paths({"trace": {"id": "t"}, "spans": [{"id": "s"}]})
    assert "spans" in paths
    assert not any(p.startswith("spans.") for p in paths)
    assert "trace.id" in paths


def test_check_names_the_valid_fields_for_an_unknown_one() -> None:
    with pytest.raises(FieldsError) as excinfo:
        projection.check(("nope",), ("id", "name", "duration"), whole="read('trace', id)")
    message = str(excinfo.value)
    assert "nope" in message
    assert "id, name, duration" in message


def test_marker_counts_the_whole_and_names_what_went() -> None:
    line = projection.marker(kept=("a",), omitted=("b", "c"), whole="Drop fields= for the rest.")
    assert line.startswith("projected: 1 of 3 fields")
    assert "omitted: b, c" in line
    assert line.endswith("Drop fields= for the rest.")


def test_marker_caps_the_names_it_lists_and_counts_the_rest() -> None:
    omitted = tuple(f"f{i}" for i in range(projection.NAMED_OMISSIONS + 4))
    line = projection.marker(kept=("a",), omitted=omitted, whole="x")
    assert f"+{4} more" in line


def test_a_container_and_one_of_its_paths_together_keep_the_container() -> None:
    """``['trace', 'trace.output']`` is a caller widening their own mind
    mid-argument. The wider name wins, in either order, and the record it was
    read from is not touched — the answer is a copy, not the record with keys
    removed from it."""
    record = {"trace": {"id": "t", "name": "n", "output": "o"}, "spans": [1, 2]}
    for order in (["trace", "trace.output"], ["trace.output", "trace"]):
        out, _, omitted = projection.project_record(record, order, whole="x")
        assert out == {"trace": {"id": "t", "name": "n", "output": "o"}}
        assert omitted == ("spans",)
    assert record == {"trace": {"id": "t", "name": "n", "output": "o"}, "spans": [1, 2]}


# --- read ------------------------------------------------------------------ #


def _big_trace() -> FakeReadClient:
    """A trace whose whole read is the 8-10k the ticket is about."""
    body = "x" * 3_000
    spans = [
        {
            "id": f"s-{n}",
            "trace_id": TRACE,
            "name": f"step-{n}",
            "input": {"prompt": body},
            "output": {"completion": body},
            "metadata": {"model": "gpt-4o", "temperature": 0.2},
        }
        for n in range(4)
    ]
    return FakeReadClient(
        traces_by_id={
            TRACE: {
                "id": TRACE,
                "project_id": PROJECT,
                "name": "chat",
                "input": {"messages": [{"role": "user", "content": body}]},
                "output": {"content": body},
                "metadata": {"env": "prod", "region": "eu"},
                "feedback_scores": [{"name": "helpfulness", "value": 0.4}],
            }
        },
        trace_spans={TRACE: spans},
    )


READ_CEILING_BYTES = 600
"""What one named field of a trace may cost, marker and header included.

The ticket's number is a trace read costing 8,000 to 10,000 tokens when one
field of one record was wanted. This fixture's whole read is larger than that;
the ceiling is what the same question costs once it can be asked.
"""


@pytest.mark.anyio
async def test_read_returns_only_the_named_fields_under_the_ceiling() -> None:
    fake = _big_trace()
    whole = await run_read("trace", TRACE, client=fake)
    projected = await run_read(
        "trace", TRACE, fields=["trace.feedback_scores", "trace.name"], client=fake
    )

    assert len(whole) > 30_000
    assert len(projected) < READ_CEILING_BYTES, projected
    payload = json.loads(projected.splitlines()[-1])
    assert set(payload) == {"trace"}
    assert set(payload["trace"]) == {"id", "name", "feedback_scores"}
    assert payload["trace"]["feedback_scores"] == [{"name": "helpfulness", "value": 0.4}]


@pytest.mark.anyio
async def test_read_keeps_the_id_that_opens_the_next_level() -> None:
    """``trace.id`` is not requested and is there anyway: a projected record
    nobody can address again is a dead end."""
    fake = _big_trace()
    out = await run_read("trace", TRACE, fields=["trace.name"], client=fake)
    assert json.loads(out.splitlines()[-1])["trace"]["id"] == TRACE


@pytest.mark.anyio
async def test_read_keeps_an_array_whole() -> None:
    fake = _big_trace()
    out = await run_read("trace", TRACE, fields=["spans"], client=fake)
    payload = json.loads(out.splitlines()[-1])
    assert len(payload["spans"]) == 4
    assert payload["spans"][0]["input"] == {"prompt": "x" * 3_000}


@pytest.mark.anyio
async def test_a_projected_read_says_so_in_the_header_and_under_it() -> None:
    """Spec D3: a projected answer may never be mistakable for a whole one.
    It is said twice — in the header a skim reads, and in a line that names
    what went."""
    out = await run_read("trace", TRACE, fields=["trace.output"], client=_big_trace())
    header, marker = out.splitlines()[0], out.splitlines()[1]
    assert "projected" in header
    # Two, not one: the id is in the payload, so it is in the count.
    assert marker.startswith("projected: 2 of ")
    assert "omitted:" in marker
    assert "spans" in marker
    assert "read('trace', '" in marker


@pytest.mark.anyio
async def test_an_unprojected_read_carries_no_marker() -> None:
    out = await run_read("trace", TRACE, client=_big_trace())
    assert "projected" not in out.splitlines()[0]
    assert not out.splitlines()[1].startswith("projected:")


@pytest.mark.anyio
async def test_read_refuses_an_unknown_field_and_names_the_valid_ones() -> None:
    with pytest.raises(ToolError) as excinfo:
        await run_read("trace", TRACE, fields=["trace.outupt"], client=_big_trace())
    message = str(excinfo.value)
    assert "trace.outupt" in message
    assert "trace.output" in message
    assert "spans" in message


@pytest.mark.anyio
async def test_read_takes_a_flat_record_by_its_own_keys() -> None:
    fake = FakeReadClient(spans_by_id={"s-1": {"id": "s-1", "name": "llm", "output": {"a": 1}}})
    out = await run_read("span", "s-1", fields=["name"], client=fake)
    payload = json.loads(out.splitlines()[-1])
    assert payload == {"id": "s-1", "name": "llm"}


# --- list: naming the fields a page carries --------------------------------- #


@pytest.mark.anyio
async def test_every_list_page_names_the_fields_its_records_carry() -> None:
    fake = FakeOpikClient(
        projects={
            "content": [{"id": "p-1", "name": "demo", "created_at": "2026-01-01"}],
            "total": 1,
        }
    )
    out = await run_list("project", client=fake)
    line = next(one for one in out.splitlines() if one.startswith("fields:"))
    assert "created_at" in line and "id" in line and "name" in line
    assert "fields=[" in line


@pytest.mark.anyio
async def test_the_fields_line_names_nested_paths_the_columns_never_show() -> None:
    """The point of the line: ``data.question`` is a path the caller would
    otherwise have to guess."""
    fake = FakeOpikClient(
        dataset_items={
            "content": [{"id": "i-1", "data": {"question": "q", "answer": "a"}}],
            "total": 1,
        }
    )
    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)
    line = next(one for one in out.splitlines() if one.startswith("fields:"))
    assert "data.question" in line and "data.answer" in line


@pytest.mark.anyio
async def test_an_empty_page_carries_no_fields_line() -> None:
    """There are no records, so there is nothing to name — and a list of
    fields nobody's rows have is a guess dressed as a fact."""
    out = await run_list("project", client=FakeOpikClient())
    assert "fields:" not in out


@pytest.mark.anyio
async def test_a_nested_key_past_the_printed_menu_is_still_accepted() -> None:
    """The regression this review found. ``metadata`` is offered, its keys are
    printed only up to the cap, and a key past it resolves on the row — so
    refusing it was a refusal the record in the caller's hand contradicts."""
    wide = {f"k{i:03d}": i for i in range(projection.MAX_NESTED_NAMES + 5)}
    fake = FakeOpikClient(traces={"content": [{"id": "t-1", "metadata": wide}], "total": 1})
    late = f"metadata.k{projection.MAX_NESTED_NAMES + 2:03d}"

    offer = next(
        one
        for one in (await run_list("trace", project_id=PROJECT, client=fake)).splitlines()
        if one.startswith("fields:")
    )
    assert late not in offer, "the premise: this key is past what the line prints"
    assert "more" in offer, "and the line says it left some out"

    out = await run_list("trace", project_id=PROJECT, fields=[late], client=fake)
    assert f"id | {late}" in out
    assert f"t-1 | {projection.MAX_NESTED_NAMES + 2}" in out


@pytest.mark.anyio
async def test_a_kept_container_is_not_reported_as_omitting_its_own_children() -> None:
    """The other regression. Naming ``feedback_scores`` renders every score in
    that cell; the marker listed ``feedback_scores.helpfulness`` as omitted
    beside the cell showing it — the one line whose whole job is to be true
    about what went, contradicting the table above it."""
    fake = FakeOpikClient(
        traces={
            "content": [
                {
                    "id": "t-1",
                    "name": "a",
                    "feedback_scores": [{"name": "helpfulness", "value": 0.9}],
                }
            ],
            "total": 1,
        }
    )
    out = await run_list("trace", project_id=PROJECT, fields=["feedback_scores"], client=fake)
    assert "t-1 | helpfulness=0.9" in out
    marker = next(one for one in out.splitlines() if one.startswith("projected:"))
    assert "feedback_scores.helpfulness" not in marker
    assert "omitted: name" in marker


@pytest.mark.anyio
async def test_a_data_key_holding_a_newline_does_not_split_the_fields_line() -> None:
    """A field name is a key the user chose. One with a line break in it broke
    the offer line into two, making the page's shape depend on what someone
    put in a dataset — so the line is escaped the way the table header is."""
    fake = FakeOpikClient(
        dataset_items={"content": [{"id": "i-1", "data": {"a|b": "x", "c\nd": "y"}}], "total": 1}
    )
    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)
    assert sum(1 for one in out.splitlines() if one.startswith("fields:")) == 1
    offer = next(one for one in out.splitlines() if one.startswith("fields:"))
    assert offer.endswith("uncut.")
    assert "|" not in offer.replace("fields=[…]", "")


# --- list: projecting -------------------------------------------------------- #


def _trace_page() -> FakeOpikClient:
    return FakeOpikClient(
        traces={
            "content": [
                {
                    "id": "t-1",
                    "name": "chat",
                    "start_time": "2026-09-01T00:00:00Z",
                    "duration": 120.0,
                    "total_estimated_cost": 0.01,
                    "usage": {"total_tokens": 900},
                    "feedback_scores": [{"name": "helpfulness", "value": 0.9}],
                }
            ],
            "total": 1,
        }
    )


@pytest.mark.anyio
async def test_list_returns_the_named_columns_and_the_id_and_nothing_else() -> None:
    out = await run_list(
        "trace",
        project_id=PROJECT,
        fields=["usage.total_tokens"],
        client=_trace_page(),
    )
    columns = next(one for one in out.splitlines() if one.startswith("id |"))
    assert columns == "id | usage.total_tokens"
    # The row is the two cells and nothing else — the dropped columns survive
    # only as names in the marker, which is where they belong.
    assert out.splitlines()[out.splitlines().index(columns) + 1] == "t-1 | 900"


@pytest.mark.anyio
async def test_projected_columns_keep_the_order_the_caller_named() -> None:
    out = await run_list(
        "trace",
        project_id=PROJECT,
        fields=["feedback_scores.helpfulness", "name"],
        client=_trace_page(),
    )
    assert "id | feedback_scores.helpfulness | name" in out


@pytest.mark.anyio
async def test_a_projected_list_page_says_so_and_names_what_it_left_out() -> None:
    out = await run_list("trace", project_id=PROJECT, fields=["name"], client=_trace_page())
    marker = next(one for one in out.splitlines() if one.startswith("projected:"))
    assert " of " in marker and "fields" in marker
    assert "start_time" in marker
    # The marker accounts for every field, so the page does not also carry the
    # offer line an unprojected page ends with.
    assert not any(one.startswith("fields:") for one in out.splitlines())


@pytest.mark.anyio
async def test_the_header_echoes_the_fields_that_were_applied() -> None:
    out = await run_list("trace", project_id=PROJECT, fields=["name"], client=_trace_page())
    assert out.splitlines()[0].startswith("[list: trace |")
    assert "fields: name" in out.splitlines()[0]


@pytest.mark.anyio
async def test_projection_lifts_the_cell_cut_on_the_columns_it_keeps() -> None:
    """A named field comes back uncut — that is what "returns exactly those
    fields" means, and the table's 60-char cell would otherwise undo it."""
    long = "y" * 500
    fake = FakeOpikClient(traces={"content": [{"id": "t-1", "name": long}], "total": 1})
    cut = await run_list("trace", project_id=PROJECT, client=fake)
    assert "..." in cut and "1 value cut at 60 chars." in cut

    whole = await run_list("trace", project_id=PROJECT, fields=["name"], client=fake)
    assert long in whole
    assert "cut at" not in whole


@pytest.mark.anyio
async def test_list_refuses_an_unknown_field_and_names_the_valid_ones() -> None:
    with pytest.raises(ToolError) as excinfo:
        await run_list(
            "trace", project_id=PROJECT, fields=["usage.totl_tokens"], client=_trace_page()
        )
    message = str(excinfo.value)
    assert "usage.totl_tokens" in message
    assert "usage.total_tokens" in message


@pytest.mark.anyio
async def test_an_experiment_row_keeps_the_prompt_version_it_was_not_asked_for() -> None:
    """The id that opens the next level for an experiment is the version of
    the prompt it ran; without it the row cannot be traced back to one."""
    fake = FakeOpikClient(
        experiments={
            "content": [
                {
                    "id": "e-1",
                    "name": "rerank-v3",
                    "dataset_name": "support-qa",
                    "trace_count": 20,
                    "prompt_versions": [{"version_number": 3}],
                }
            ],
            "total": 1,
        }
    )
    out = await run_list("experiment", fields=["dataset_name"], client=fake)
    assert "id | dataset_name | prompt_version" in out


@pytest.mark.anyio
async def test_a_dataset_item_row_keeps_its_id() -> None:
    fake = FakeOpikClient(
        dataset_items={
            "content": [{"id": "i-1", "data": {"question": "q", "answer": "a"}}],
            "total": 1,
        }
    )
    out = await run_list("dataset_item", dataset_id=DATASET, fields=["data.question"], client=fake)
    assert "id | data.question" in out
    assert "data.answer" not in out.replace("omitted:", "").split("projected:")[0]


# --- list: the comparison ----------------------------------------------------- #


@pytest.mark.anyio
async def test_comparison_returns_the_named_case_key_and_score_and_the_trace() -> None:
    """The ticket's own example: two experiments, one question, one score —
    not N experiments times M keys."""
    case = _case(
        "case-1",
        {"question": "Capital of France?", "expected_answer": "Paris", "notes": "n"},
        [
            _run(A, trace="tr-a", scores={"helpfulness": 0.9, "correctness": 0.8}),
            _run(B, trace="tr-b", scores={"helpfulness": 0.4, "correctness": 0.2}),
        ],
    )
    out = await run_list(
        "dataset_item",
        experiment_ids=[A, B],
        fields=["data.question", "feedback_scores.helpfulness"],
        client=_fake(case),
    )
    columns = next(one for one in out.splitlines() if one.startswith("id |"))
    # The score column keeps the "direction unknown" marking OPIK-8394 put on
    # every column whose cell carries a Δ. A projection narrows which columns
    # are shown; it never changes what a shown cell means.
    assert columns == "id | data.question | helpfulness (direction unknown) | worst_trace"
    assert "correctness" not in columns
    assert "expected_answer" not in columns
    assert any(one.startswith("projected:") for one in out.splitlines())


@pytest.mark.anyio
async def test_comparison_refuses_an_unknown_field_and_names_the_valid_ones() -> None:
    case = _case(
        "case-1",
        {"question": "Capital of France?"},
        [_run(A, trace="tr-a", scores={"helpfulness": 0.9})],
    )
    with pytest.raises(ToolError) as excinfo:
        await run_list(
            "dataset_item",
            experiment_ids=[A, B],
            fields=["data.quesiton"],
            client=_fake(case),
        )
    message = str(excinfo.value)
    assert "data.quesiton" in message
    assert "data.question" in message
    assert "feedback_scores.helpfulness" in message


@pytest.mark.anyio
async def test_a_derived_column_is_nameable_like_any_other() -> None:
    """``error_type`` is read out of the error container by the renderer, not
    carried by the record. The table shows it, so ``fields`` has to take it —
    a column the caller can see and cannot ask for is the naming rule failing
    on our own field."""
    fake = FakeOpikClient(
        traces={
            "content": [
                {"id": "t-1", "name": "a", "error_info": {"exception_type": "ValueError"}},
                {"id": "t-2", "name": "b"},
            ],
            "total": 2,
        }
    )
    assert "error_type" in next(
        one
        for one in (await run_list("trace", project_id=PROJECT, client=fake)).splitlines()
        if one.startswith("fields:")
    )
    out = await run_list("trace", project_id=PROJECT, fields=["error_type"], client=fake)
    assert "id | error_type" in out
    assert "t-1 | ValueError" in out
    # The row that has no error still has the column; a blank is a trace that
    # did not fail, which is the whole reason the column is worth asking for.
    assert "t-2 | " in out


@pytest.mark.anyio
async def test_a_metric_series_refuses_fields_rather_than_ignoring_them() -> None:
    """A bucket is not a record, so there is no field of one to name. Refused
    for the same reason page/size/sort are: a projection that quietly did
    nothing reads as a projection that found nothing."""
    fake = FakeOpikClient(projects={"content": [{"id": PROJECT, "name": "demo"}], "total": 1})
    with pytest.raises(ToolError) as excinfo:
        await run_list(
            "project_metric",
            project_id=PROJECT,
            metric_type="trace_count",
            fields=["value"],
            client=fake,
        )
    assert "fields" in str(excinfo.value)


@pytest.mark.anyio
async def test_an_entity_addressed_by_name_leads_with_the_name_not_an_empty_id() -> None:
    """``score_name`` has no id: the name is what addresses the record, so it
    is the handle the projection keeps. A page of bare counts would be the
    dead end this rule exists to prevent, in the one place where leading with
    ``id`` would have printed a column of nothing."""
    fake = FakeOpikClient(score_names={"scores": [{"name": "helpfulness", "count": 12}]})
    out = await run_list("score_name", project_id=PROJECT, fields=["count"], client=fake)
    assert "name | count" in out
    assert "helpfulness | 12" in out


# --- the argument itself ------------------------------------------------------- #


@pytest.mark.anyio
async def test_no_fields_is_the_page_as_it_was() -> None:
    fake = _trace_page()
    before = await run_list("trace", project_id=PROJECT, client=fake)
    after = await run_list("trace", project_id=PROJECT, fields=[], client=fake)
    assert before == after
    assert "projected:" not in after


def test_the_two_new_parameter_descriptions_stay_tight() -> None:
    """The tool surface rides in the context of every request the host makes.
    Two arguments are worth a budget line; two paragraphs are not."""
    from opik_mcp.server import FIELDS_LIST_DESCRIPTION, FIELDS_READ_DESCRIPTION

    assert len(FIELDS_READ_DESCRIPTION) + len(FIELDS_LIST_DESCRIPTION) <= 700
