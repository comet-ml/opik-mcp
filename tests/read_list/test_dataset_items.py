"""``list('dataset_item')`` — columns discovered from the items' ``data``.

A dataset item has no fixed fields: its payload is a ``data`` map whose keys
the user chose. The projection reads the columns off the page, ranks them,
and states every cut it makes. These tests pin that contract; the live bug
they guard against is a hardcoded ``input``/``expected_output`` projection
that rendered twenty rows of nothing for a ``question``/``answer`` dataset.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.entities.dataset import ITEM_HANDLER, project_items
from opik_mcp.read_list.entities.dataset.items import (
    _CELL_CEILING,
    _CELL_FLOOR,
    _MAX_DATA_COLUMNS,
    _PAGE_DATA_BUDGET,
)
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.read_list.reference import list_reference
from opik_mcp.read_list.registry import ENTITY_REGISTRY, READABLE_TYPES

from .test_list_tool import FakeOpikClient
from .test_read_tool import FakeOpikClient as FakeReadClient

DATASET = "019f8d97-c83c-7597-b40a-bd2e0e1ad558"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _item(item_id: str, **data: Any) -> dict[str, Any]:
    return {"id": item_id, "source": "manual", "tags": [], "data": data}


def _page(*items: dict[str, Any]) -> dict[str, Any]:
    return {"content": list(items), "total": len(items)}


# --- the projection ----------------------------------------------------- #


def test_columns_are_the_data_keys_of_the_page() -> None:
    """The reported dataset: ``question``/``answer``/``expected_behavior``,
    none of which a fixed projection could have named."""
    page = [
        _item(
            "i-1",
            question="How do I install?",
            answer="pip install opik",
            expected_behavior="answer",
        ),
        _item(
            "i-2", question="Weather today?", answer="I focus on Opik.", expected_behavior="decline"
        ),
    ]
    projection = project_items(page)
    assert projection.columns == ("data.answer", "data.expected_behavior", "data.question")
    assert projection.note == "Columns after id are the items' data keys (all 3 on this page)."


def test_a_dataset_with_other_keys_renders_those_keys() -> None:
    projection = project_items([_item("i-1", prompt="p", completion="c", score=0.5)])
    assert projection.columns == ("data.completion", "data.prompt", "data.score")


def test_documented_sdk_keys_come_first_in_their_documented_order() -> None:
    """``input`` / ``expected_output`` lead when present, whatever their fill;
    the rest of the keys follow by fill rate, then name."""
    page = [
        _item("i-1", zeta="z", expected_output="e", input="i", alpha="a"),
        _item("i-2", zeta="z", expected_output="e", input="i"),
        _item("i-3", zeta="z", input="i"),
    ]
    projection = project_items(page)
    assert projection.columns == ("data.input", "data.expected_output", "data.zeta", "data.alpha")


def test_keys_missing_on_some_rows_rank_by_fill_then_name() -> None:
    page = [
        _item("i-1", common="1", rare="r", mid="m"),
        _item("i-2", common="2", mid="m"),
        _item("i-3", common="3"),
    ]
    projection = project_items(page)
    assert projection.columns == ("data.common", "data.mid", "data.rare")


def test_null_values_count_as_unfilled_but_keep_the_column() -> None:
    page = [
        _item("i-1", a=None, b="b"),
        _item("i-2", a=None, b="b"),
    ]
    projection = project_items(page)
    # ``a`` is on every row but never filled; it ranks under ``b`` and stays.
    assert projection.columns == ("data.b", "data.a")


def test_wide_union_is_capped_and_the_cut_is_declared_with_the_omitted_keys() -> None:
    keys = {f"k{i:02d}": "v" for i in range(_MAX_DATA_COLUMNS + 4)}
    projection = project_items([_item("i-1", **keys)])
    assert len(projection.columns) == _MAX_DATA_COLUMNS
    assert projection.columns[0] == "data.k00"
    assert projection.note is not None
    assert f"showing {_MAX_DATA_COLUMNS} of {_MAX_DATA_COLUMNS + 4} by fill rate" in projection.note
    omitted = ", ".join(f"k{i:02d}" for i in range(_MAX_DATA_COLUMNS, _MAX_DATA_COLUMNS + 4))
    assert projection.note.endswith(f"omitted: {omitted}.")


def test_cell_cap_scales_with_rows_and_columns() -> None:
    """The page's data budget is split across its cells, so a narrower page
    shows more of each value. That is the drill-down for a long value: an item
    has no read of its own and the server keeps no copy to fetch the rest from."""
    two_cols = [_item(f"i-{n}", q="q", a="a") for n in range(20)]
    assert project_items(two_cols).cell_limit == _PAGE_DATA_BUDGET // (20 * 2)
    assert project_items(two_cols[:1]).cell_limit == _CELL_CEILING
    many_cols = [_item(f"i-{n}", **{f"k{i}": "v" for i in range(8)}) for n in range(100)]
    assert project_items(many_cols).cell_limit == _CELL_FLOOR


def test_empty_page_projects_no_columns_and_says_so() -> None:
    projection = project_items([])
    assert projection.columns == ()
    assert projection.note == "These items carry no data keys."


def test_rows_without_a_data_map_are_skipped_not_fatal() -> None:
    projection = project_items([{"id": "i-1", "data": "not a map"}, _item("i-2", q="q")])
    assert projection.columns == ("data.q",)


# --- through the list tool ---------------------------------------------- #


@pytest.mark.anyio
async def test_list_renders_the_items_content_for_every_row() -> None:
    fake = FakeOpikClient(
        dataset_items=_page(
            _item(
                "i-1",
                question="How can I evaluate my LLM outputs using Opik?",
                answer="Use evaluate().",
                expected_behavior="answer",
            ),
            _item(
                "i-2",
                question="Tell me a joke",
                answer="I focus on Opik.",
                expected_behavior="decline",
            ),
        )
    )
    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)
    assert fake.last_kwargs["dataset_id"] == DATASET
    lines = out.splitlines()
    assert lines[3] == "id | data.answer | data.expected_behavior | data.question"
    assert (
        lines[4] == "i-1 | Use evaluate(). | answer | How can I evaluate my LLM outputs using Opik?"
    )
    assert lines[5] == "i-2 | I focus on Opik. | decline | Tell me a joke"
    # No always-empty ``name`` column, and no phantom input/expected_output.
    assert "name" not in lines[3]
    assert "expected_output" not in out
    assert "Columns after id are the items' data keys (all 3 on this page)." in out


@pytest.mark.anyio
async def test_list_renders_a_row_missing_a_key_as_an_empty_cell() -> None:
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", q="q1", a="a1"), _item("i-2", q="q2")))
    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)
    # ``q`` fills both rows and ``a`` one, so ``q`` leads; the gap is an empty cell.
    assert "i-1 | q1 | a1" in out
    assert "i-2 | q2 | " in out


@pytest.mark.anyio
async def test_list_cuts_long_values_and_declares_the_cut() -> None:
    long = "x" * 5_000
    page = _page(*(_item(f"i-{n}", q=long, a="short") for n in range(20)))
    fake = FakeOpikClient(dataset_items=page)
    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)
    cap = _PAGE_DATA_BUDGET // (20 * 2)
    assert long not in out
    assert "x" * (cap - 3) + "..." in out
    assert (
        f"20 values cut at {cap} chars; fewer rows per page (size=…) raise the cap, "
        "and read('dataset_item', id) is the value whole." in out
    )


@pytest.mark.anyio
async def test_list_shows_more_of_each_value_on_a_smaller_page() -> None:
    long = "x" * 500
    fake = FakeOpikClient(dataset_items={"content": [_item("i-1", q=long, a="a")], "total": 20})
    out = await run_list("dataset_item", dataset_id=DATASET, size=1, client=fake)
    assert long in out
    assert "cut at" not in out
    assert "Use page=2 for next 1 results." in out


@pytest.mark.anyio
async def test_list_renders_nested_values_as_compact_json() -> None:
    fake = FakeOpikClient(
        dataset_items=_page(
            _item(
                "i-1",
                input={"messages": [{"role": "user", "content": "hi"}]},
                expected_output=["a", "b"],
                n=3,
            )
        )
    )
    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)
    assert 'i-1 | {"messages":[{"role":"user","content":"hi"}]} | ["a","b"] | 3' in out
    assert "[object]" not in out
    assert "{'" not in out


@pytest.mark.anyio
async def test_list_declares_the_column_cut_under_the_table() -> None:
    keys = {f"k{i:02d}": "v" for i in range(_MAX_DATA_COLUMNS + 2)}
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", **keys)))
    out = await run_list("dataset_item", dataset_id=DATASET, client=fake)
    header = out.splitlines()[3]
    assert header.count(" | ") == _MAX_DATA_COLUMNS  # id + capped data columns
    assert f"showing {_MAX_DATA_COLUMNS} of {_MAX_DATA_COLUMNS + 2} by fill rate" in out
    assert f"omitted: k{_MAX_DATA_COLUMNS:02d}, k{_MAX_DATA_COLUMNS + 1:02d}." in out


@pytest.mark.anyio
async def test_list_empty_page_is_the_plain_empty_message() -> None:
    out = await run_list("dataset_item", dataset_id=DATASET, client=FakeOpikClient())
    assert out.splitlines()[1:] == ["No dataset_items found."]


# --- the registry row --------------------------------------------------- #


def test_item_handler_projects_rather_than_declaring_fields() -> None:
    handler = ENTITY_REGISTRY["dataset_item"]
    assert handler is ITEM_HANDLER
    assert handler.list_projection_fn is project_items
    assert handler.list_extra_fields == ()
    assert handler.list_has_name is False


def test_item_handler_description_matches_the_code() -> None:
    """The description is a comment nobody renders, so this is the only place
    a drift would show. It has been wrong twice: it promised items inline on
    ``read('dataset')`` that nothing ever inlined, and it went on saying there
    was no read of an item after one was added."""
    assert "read('dataset_item', id) is one case whole" in ITEM_HANDLER.description
    assert "the endpoint has no sorting" in ITEM_HANDLER.description
    assert "up to 200 items inline" not in ITEM_HANDLER.description
    assert "there is no read" not in ITEM_HANDLER.description


# --- finding one case in a large dataset -------------------------------- #
#
# The items endpoint takes ``filters`` and nothing else: no ``search``, no
# ``sorting``. Its fields are the case's own — the keys of its ``data`` map,
# the whole payload as one string, its id, tags, source and the trace or span
# it was made from — and they are not the fields of the comparison, which
# reads the runs. The two vocabularies are kept apart so an operator the one
# endpoint refuses cannot be written for the other.

_TRACE = "0199c6a4-3a4c-7f1e-9d2b-000000000030"
_SPAN = "0199c6a4-3a4c-7f1e-9d2b-000000000031"
_CASE = "0199c6a4-3a4c-7f1e-9d2b-000000000040"


def _clauses(fake: FakeOpikClient) -> Any:
    return json.loads(fake.last_kwargs["filters"])


@pytest.mark.anyio
async def test_a_case_key_travels_as_the_backends_map_filter() -> None:
    """``data`` is a MAP column: the key rides beside the field rather than
    being spliced into its name, which is what the comparison's dynamic
    columns do. Send the wrong one and the backend filters on nothing."""
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", question="How do I install?")))

    out = await run_list(
        "dataset_item",
        dataset_id=DATASET,
        filters='data.question contains "install"',
        client=fake,
    )

    assert _clauses(fake) == [
        {"field": "data", "operator": "contains", "key": "question", "value": "install"}
    ]
    assert re.fullmatch(
        r'\[list: dataset_item \| [\d,]+ tok \| filters: data.question contains "install"\]',
        out.splitlines()[0],
    )
    assert "i-1 | How do I install?" in out


@pytest.mark.anyio
@pytest.mark.parametrize("comparison", ["data.score > 0.5", "data.score < 1"])
async def test_a_comparison_on_a_case_key_is_refused_before_the_call(comparison: str) -> None:
    """opik-backend maps six string operators onto MAP and answers 400 for the
    rest, so the refusal is worth more here than the round trip."""
    fake = FakeOpikClient()

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", dataset_id=DATASET, filters=comparison, client=fake)

    message = str(refusal.value)
    assert "is not valid for 'data' (map)" in message
    assert "Valid: =, !=, contains, not_contains, starts_with, ends_with." in message
    assert fake.last_kwargs == {}


@pytest.mark.anyio
async def test_a_case_key_filter_without_a_key_says_how_to_write_one() -> None:
    fake = FakeOpikClient()

    with pytest.raises(ToolError) as refusal:
        await run_list(
            "dataset_item", dataset_id=DATASET, filters='data contains "install"', client=fake
        )

    assert "'data' needs a key: write data.<key>" in str(refusal.value)
    assert fake.last_kwargs == {}


@pytest.mark.anyio
async def test_the_source_trace_finds_the_case_that_was_made_from_it() -> None:
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", question="q")))

    out = await run_list(
        "dataset_item", dataset_id=DATASET, filters=f'trace_id = "{_TRACE}"', client=fake
    )

    assert _clauses(fake) == [{"field": "trace_id", "operator": "=", "key": "", "value": _TRACE}]
    # One value on every row it selected: the header states it, the table
    # spends no column on it.
    assert "trace_id" not in out.splitlines()[2]
    assert f'filters: trace_id = "{_TRACE}"' in out.splitlines()[0]


@pytest.mark.anyio
async def test_the_whole_payload_is_searchable_without_naming_a_key() -> None:
    """``full_data`` is the endpoint's substitute for free text: one ilike over
    the serialised map, for the caller who does not know which key holds it."""
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", question="q", answer="a")))

    out = await run_list(
        "dataset_item", dataset_id=DATASET, filters='full_data contains "refund"', client=fake
    )

    assert _clauses(fake) == [
        {"field": "full_data", "operator": "contains", "key": "", "value": "refund"}
    ]
    # Not a column: the whole payload is what the data columns already show.
    assert "full_data" not in out.splitlines()[2]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (f'id = "{_CASE}"', {"field": "id", "operator": "=", "key": "", "value": _CASE}),
        (f'span_id = "{_SPAN}"', {"field": "span_id", "operator": "=", "key": "", "value": _SPAN}),
        (
            'tags contains "regression"',
            {"field": "tags", "operator": "contains", "key": "", "value": "regression"},
        ),
        (
            'source = "trace"',
            {"field": "source", "operator": "=", "key": "", "value": "trace"},
        ),
        (
            'created_at >= "2026-09-08T00:00:00Z"',
            {
                "field": "created_at",
                "operator": ">=",
                "key": "",
                "value": "2026-09-08T00:00:00Z",
            },
        ),
    ],
)
async def test_the_rest_of_the_item_fields_compile_to_the_backends_shape(
    filters: str, expected: dict[str, str]
) -> None:
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", question="q")))

    await run_list("dataset_item", dataset_id=DATASET, filters=filters, client=fake)

    assert _clauses(fake) == [expected]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "compare_only",
    ["duration > 1", 'output contains "sorry"', "feedback_scores.correctness < 0.5"],
)
async def test_a_field_of_the_comparison_points_the_caller_at_experiment_ids(
    compare_only: str,
) -> None:
    """These read the runs, which a plain list of a dataset's cases has none
    of. The refusal names the call that does have them rather than leaving the
    caller to read a field list and guess why theirs is missing."""
    fake = FakeOpikClient()

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", dataset_id=DATASET, filters=compare_only, client=fake)

    message = str(refusal.value)
    assert "Unknown field" in message
    assert "experiment_ids" in message
    assert fake.last_kwargs == {}


@pytest.mark.anyio
async def test_sort_is_refused_because_the_items_endpoint_has_no_sorting() -> None:
    """The backend takes no ``sorting`` parameter here, so an accepted sort
    would be an unordered page that reads as an ordered one."""
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", question="q")))

    with pytest.raises(ToolError) as refusal:
        await run_list("dataset_item", dataset_id=DATASET, sort="created_at desc", client=fake)

    message = str(refusal.value)
    assert "sort is not supported" in message
    assert "experiment_ids" in message
    assert fake.last_kwargs == {}


@pytest.mark.anyio
async def test_an_unfiltered_page_sends_no_filters_at_all() -> None:
    fake = FakeOpikClient(dataset_items=_page(_item("i-1", question="q")))

    await run_list("dataset_item", dataset_id=DATASET, client=fake)

    assert "filters" not in fake.last_kwargs


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("argument", "value", "refusal", "points_at_the_table"),
    [
        ("filters", "data.score > 1", "Invalid filters for dataset_item:", True),
        ("search", "Japan", "search is not supported for 'dataset_item'", True),
        # Sort points at the call that orders cases instead: the table it
        # would send the caller to lists no sortable field at all.
        ("sort", "created_at desc", "sort is not supported for 'dataset_item'", False),
    ],
)
async def test_a_refusal_names_the_entity_the_caller_typed(
    argument: str, value: str, refusal: str, points_at_the_table: bool
) -> None:
    """``dataset_item_case`` is the name of a field table, not of anything a
    caller can pass. A refusal that named it would read as a typo the caller
    could not have made — so the refusal names the entity, and where a field
    table is what the caller needs next, the pointer beside it names that."""
    asked: dict[str, Any] = {argument: value}
    with pytest.raises(ToolError) as err:
        await run_list(
            "dataset_item",
            dataset_id=DATASET,
            client=FakeOpikClient(),
            filters=asked.get("filters"),
            sort=asked.get("sort"),
            search=asked.get("search"),
        )

    message = str(err.value)
    assert refusal in message
    assert "dataset_item_case" not in message.split("schema(")[0]
    assert ('schema("list.dataset_item_case")' in message) is points_at_the_table


def test_each_reference_names_the_fields_of_the_other_call() -> None:
    """One entity, two field tables, and an agent asks for whichever name it
    knows. Whichever it lands on has to say that the other call exists and
    what could be asked there — a bare pointer would cost a round trip to find
    out that ``data.question`` is available without experiments."""
    compared = list_reference("dataset_item")["filters"]["see_also"]
    case = list_reference("dataset_item_case")["filters"]["see_also"]

    assert "full_data" in compared and "trace_id" in compared
    assert 'schema("list.dataset_item_case")' in compared
    assert "feedback_scores" in case and "duration" in case
    assert 'schema("list.dataset_item")' in case


def test_the_schema_publishes_the_fields_of_a_case_with_their_operators() -> None:
    reference = list_reference("dataset_item_case")
    fields = reference["filters"]["fields"]

    assert sorted(fields) == [
        "created_at",
        "created_by",
        "data",
        "full_data",
        "id",
        "last_updated_at",
        "last_updated_by",
        "source",
        "span_id",
        "tags",
        "trace_id",
    ]
    assert fields["data"]["type"] == "map"
    assert fields["data"]["operators"] == [
        "=",
        "!=",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
    ]
    assert fields["data"]["key"] == "required"
    # The one field that costs a full scan says so where it is chosen, not
    # after it has been run on a 100,000-case dataset.
    assert "scan" in fields["full_data"]["note"]
    # An empty field list alone reads as "not implemented yet", so the
    # reference says whose limit it is and what does order cases.
    assert reference["sort"]["fields"] == []
    assert reference["sort"]["why"] == (
        "opik-backend's dataset items endpoint takes no sorting parameter. Only the "
        "comparison orders cases, so a sort needs experiment_ids: "
        "list('dataset_item', experiment_ids=['<uuid>', '<uuid>'], sort='duration desc')"
    )
    assert reference["search"] is False


# --- read('dataset_item', id): the case, uncut -------------------------- #


def test_a_case_is_readable_now_that_the_backend_addresses_it_on_its_own() -> None:
    assert "dataset_item" in READABLE_TYPES


@pytest.mark.anyio
async def test_read_returns_the_whole_value_the_list_cut() -> None:
    """The page's character budget is split across its cells, so a long case
    arrives cut. This is where the rest of it is: the endpoint the list could
    not offer before, addressed by the id the table printed."""
    long = "x" * 5_000
    listed = FakeOpikClient(
        dataset_items=_page(*(_item(f"i-{n}", q=long, a="short") for n in range(20)))
    )
    table = await run_list("dataset_item", dataset_id=DATASET, client=listed)
    assert long not in table

    record = {"id": _CASE, "data": {"q": long, "a": "short"}, "source": "manual"}
    fake = FakeReadClient(dataset_items_by_id={_CASE: record})
    out = await run_read("dataset_item", _CASE, client=fake)

    assert out.splitlines()[0].startswith(f"[read: dataset_item {_CASE}")
    assert json.loads(out.split("\n", 1)[1]) == record


@pytest.mark.anyio
async def test_the_test_suite_item_alias_still_reads_and_lists() -> None:
    """A suite is a dataset and its cases are dataset items; the old names are
    what callers with a running prompt still type."""
    record = {"id": _CASE, "data": {"question": "How do I install?"}}
    fake = FakeReadClient(dataset_items_by_id={_CASE: record})

    out = await run_read("test_suite_item", _CASE, client=fake)

    assert fake.fetched_items == [_CASE]
    assert json.loads(out.split("\n", 1)[1]) == record


@pytest.mark.anyio
async def test_reading_a_case_that_is_not_there_names_the_id() -> None:
    with pytest.raises(ToolError) as refusal:
        await run_read("dataset_item", _CASE, client=FakeReadClient())

    assert f"Not found: dataset_item with id '{_CASE}'" in str(refusal.value)
