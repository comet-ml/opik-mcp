"""The cases of a dataset: listing them, and choosing their columns.

A dataset item has no fixed fields. Its payload is ``data``, a map keyed
however the user built the dataset, so the columns of a page are read off the
page rather than declared. Comparison mode reuses the same ranking through
:func:`data_columns`, with a tighter cap, because there the runs need the
width.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from opik_mcp.opik_client import OpikListClient
from opik_mcp.read_list.handler import ListProjection


async def list_items(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # opik-backend's items endpoint is ``/datasets/{id}/items`` — the dataset
    # id is in the path, not a query param. Pull it out before forwarding.
    dataset_id = kw.pop("dataset_id", None)
    if not dataset_id:
        raise ValueError("list dataset_item requires dataset_id")
    kw.pop("name", None)
    return await client.list_dataset_items(dataset_id, **kw)


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


def data_columns(content: list[dict[str, Any]], limit: int) -> tuple[list[str], list[str]]:
    """The items' ``data`` keys, ranked, cut to ``limit``: (shown, omitted).

    Ranked with the SDK-documented keys first, then by how many rows on the
    page fill each key, then by name, so the same page always gets the same
    columns. Comparison mode asks for a tighter limit than a plain listing:
    the runs are what the caller came for.
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
    return keys[:limit], keys[limit:]


def cell_limit(rows: int, columns: int) -> int:
    """The per-cell cut for a page, from the page's character budget.

    Fewer rows (``size=5``) or fewer columns means more of each value, so the
    way to see a long value whole is to narrow the page — an item has no read
    of its own, and this server keeps no copy of the page to fetch the rest
    from (see :mod:`opik_mcp.read_list.size`).
    """
    per_cell = _PAGE_DATA_BUDGET // (max(1, rows) * max(1, columns))
    return min(_CELL_CEILING, max(_CELL_FLOOR, per_cell))


def project_items(content: list[dict[str, Any]]) -> ListProjection:
    """The columns for one page of items: the keys of their ``data`` maps.

    A dataset item has no fixed fields. Its payload is ``data``, a map whose
    keys the user chose when they built the dataset (``question``/``answer``
    for one dataset, ``input``/``expected_output`` for the next), so the columns
    are read off the page rather than declared. The page's ``columns`` field
    from the backend is the whole dataset's key union, and
    ``/items/experiments/items/output/columns`` enumerates experiment *output*
    keys — neither says which keys this page's rows fill, so the union is taken
    here, from the rows themselves, at no extra call.
    """
    shown, omitted = data_columns(content, _MAX_DATA_COLUMNS)
    keys = [*shown, *omitted]

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
        cell_limit=cell_limit(len(content), len(shown)),
        note=note,
        cut_hint="fewer rows per page (size=…) raise the cap",
    )
