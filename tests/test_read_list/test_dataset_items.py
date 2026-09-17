"""``list('dataset_item')`` — columns discovered from the items' ``data``.

A dataset item has no fixed fields: its payload is a ``data`` map whose keys
the user chose. The projection reads the columns off the page, ranks them,
and states every cut it makes. These tests pin that contract; the live bug
they guard against is a hardcoded ``input``/``expected_output`` projection
that rendered twenty rows of nothing for a ``question``/``answer`` dataset.
"""

from __future__ import annotations

from typing import Any

import pytest

from opik_mcp.read_list.entities.dataset import ITEM_HANDLER, project_items
from opik_mcp.read_list.entities.dataset.items import (
    _CELL_CEILING,
    _CELL_FLOOR,
    _MAX_DATA_COLUMNS,
    _PAGE_DATA_BUDGET,
)
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.registry import ENTITY_REGISTRY

from .test_list_tool import FakeOpikClient

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
    assert lines[2] == "id | data.answer | data.expected_behavior | data.question"
    assert (
        lines[3] == "i-1 | Use evaluate(). | answer | How can I evaluate my LLM outputs using Opik?"
    )
    assert lines[4] == "i-2 | I focus on Opik. | decline | Tell me a joke"
    # No always-empty ``name`` column, and no phantom input/expected_output.
    assert "name" not in lines[2]
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
    assert f"20 values cut at {cap} chars; fewer rows per page (size=…) raise the cap." in out


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
    header = out.splitlines()[2]
    assert header.count(" | ") == _MAX_DATA_COLUMNS  # id + capped data columns
    assert f"showing {_MAX_DATA_COLUMNS} of {_MAX_DATA_COLUMNS + 2} by fill rate" in out
    assert f"omitted: k{_MAX_DATA_COLUMNS:02d}, k{_MAX_DATA_COLUMNS + 1:02d}." in out


@pytest.mark.anyio
async def test_list_empty_page_is_the_plain_empty_message() -> None:
    out = await run_list("dataset_item", dataset_id=DATASET, client=FakeOpikClient())
    assert out == "No dataset_items found."


# --- the registry row --------------------------------------------------- #


def test_item_handler_projects_rather_than_declaring_fields() -> None:
    handler = ENTITY_REGISTRY["dataset_item"]
    assert handler is ITEM_HANDLER
    assert handler.list_projection_fn is project_items
    assert handler.list_extra_fields == ()
    assert handler.list_has_name is False


def test_item_handler_description_matches_the_code() -> None:
    """The old description promised items inline on ``read('dataset')``;
    nothing ever inlined them. The description is a comment nobody renders,
    so this is the only place a drift would show."""
    assert (
        "read('dataset') returns the dataset record without its items" in ITEM_HANDLER.description
    )
    assert "up to 200 items inline" not in ITEM_HANDLER.description
