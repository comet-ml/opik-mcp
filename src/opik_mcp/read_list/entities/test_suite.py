"""``test_suite`` and its items — a dataset of cases, and the cases.

Two entities in one namespace because the second exists only under the first.
opik-backend addresses items as ``/datasets/{id}/items``, so an item has no
read of its own and is only ever listed under its suite.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler, ListProjection
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.unsupported import unsupported_fetch


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_test_suite(entity_id)


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_test_suites(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_test_suites(**kw)


async def list_items(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # opik-backend's items endpoint is ``/datasets/{id}/items`` — the suite id
    # is in the path, not a query param. Pull it out before forwarding.
    suite_id = kw.pop("test_suite_id", None)
    if not suite_id:
        raise ValueError("list test_suite_item requires test_suite_id")
    kw.pop("name", None)
    return await client.list_test_suite_items(suite_id, **kw)


#: The item keys Opik's SDKs document, shown first when a dataset has them.
#: Everything else is ranked by how many rows on the page fill it, then by
#: name, so the same page always gets the same columns. Nothing here is
#: required: a dataset keyed ``question``/``answer`` renders those.
_PREFERRED_KEYS = ("input", "expected_output", "output", "context", "reference")
_MAX_DATA_COLUMNS = 8
#: Characters of item data a page may spend, split across its rows and data
#: columns into a per-cell cap. Fewer rows (``size=5``) or fewer columns means
#: more of each value, so the way to see a long value whole is to narrow the
#: page — an item has no read of its own, and this server keeps no copy of the
#: page to fetch the rest from (see :mod:`opik_mcp.read_list.size`).
_PAGE_DATA_BUDGET = 8_000
_CELL_FLOOR = 60
_CELL_CEILING = 4_000


def project_items(content: list[dict[str, Any]]) -> ListProjection:
    """The columns for one page of items: the keys of their ``data`` maps.

    A dataset item has no fixed fields. Its payload is ``data``, a map whose
    keys the user chose when they built the dataset (``question``/``answer``
    for one suite, ``input``/``expected_output`` for the next), so the columns
    are read off the page rather than declared. The page's ``columns`` field
    from the backend is the whole dataset's key union, and
    ``/items/experiments/items/output/columns`` enumerates experiment *output*
    keys — neither says which keys this page's rows fill, so the union is taken
    here, from the rows themselves, at no extra call.
    """
    present: Counter[str] = Counter()
    filled: Counter[str] = Counter()
    for item in content:
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            present[key] += 1
            if value is not None:
                filled[key] += 1

    def rank(key: str) -> tuple[int, int, str]:
        preferred = _PREFERRED_KEYS.index(key) if key in _PREFERRED_KEYS else len(_PREFERRED_KEYS)
        return (preferred, -filled[key], key)

    keys = sorted(present, key=rank)
    shown, omitted = keys[:_MAX_DATA_COLUMNS], keys[_MAX_DATA_COLUMNS:]
    per_cell = _PAGE_DATA_BUDGET // (max(1, len(content)) * max(1, len(shown)))
    cell_limit = min(_CELL_CEILING, max(_CELL_FLOOR, per_cell))

    if not keys:
        note = "These items carry no data keys."
    elif omitted:
        note = (
            f"Columns after id are the items' data keys, showing {len(shown)} of "
            f"{len(keys)} by fill rate; omitted: {', '.join(omitted)}."
        )
    else:
        note = f"Columns after id are the items' data keys (all {len(keys)} on this page)."
    return ListProjection(
        columns=tuple(f"data.{key}" for key in shown),
        cell_limit=cell_limit,
        note=note,
        cut_hint="fewer rows per page (size=…) raise the cap",
    )


HANDLER = EntityHandler(
    entity_type="test_suite",
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    list_extra_fields=("created_at",),
    description=(
        "Opik 2.0 test suite (evaluation dataset). REST path is /datasets/{id} — "
        "test_suite is the conceptual name for the same backing entity."
    ),
)


ITEM_HANDLER = EntityHandler(
    entity_type="test_suite_item",
    fetch_fn=unsupported_fetch,
    list_fn=list_items,
    list_projection_fn=project_items,
    list_required_kwargs=("test_suite_id",),
    list_has_name=False,
    id_only=True,
    description=(
        "Test suite item. List-only — pass test_suite_id to enumerate; there is no read "
        "of an item, and read('test_suite') returns the suite record without its items. "
        "Columns are the items' data keys, discovered from each page."
    ),
)
