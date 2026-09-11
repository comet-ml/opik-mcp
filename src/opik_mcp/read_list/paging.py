"""Reading the backend's page envelope.

Every listable endpoint answers with the same Spring page shape, and three
questions get asked of it everywhere: what are the rows, was the collection
cut short, and which of the rows are name-match candidates. They were private
helpers in the registry, which is why an entity's own module could not use
them without importing the table of all entities.
"""

from __future__ import annotations

from typing import Any


def page_items(page_body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = page_body.get("content") or []
    return [it for it in raw if isinstance(it, dict)]


def collection_truncated(page_body: dict[str, Any], *, inlined: int, limit: int) -> bool:
    """Did the embedded collection get capped — by either us or the backend?

    Three signals, in order of trust: a ``total`` the backend stated, a
    ``size`` the page filled exactly, or our own inline limit reached.
    """
    total_raw = page_body.get("total")
    if isinstance(total_raw, int) and total_raw >= 0:
        return total_raw > inlined
    size_raw = page_body.get("size")
    if isinstance(size_raw, int) and size_raw > 0 and inlined >= size_raw:
        return True
    return inlined >= limit


def collection_total(page_body: dict[str, Any]) -> int | None:
    """The backend's ``total`` for the collection, when it stated one."""
    total_raw = page_body.get("total")
    return total_raw if isinstance(total_raw, int) and total_raw >= 0 else None


#: What ``list`` will hand out per page at most. The continuation below is
#: written against it, so an inline limit has to be a whole number of pages.
LIST_PAGE = 100


def continuation(inline_limit: int) -> str:
    """``page=N, size=100`` for the first page past an inlined collection.

    Written once because it was written three times, and one of the three had
    the page number typed in by hand — correct until the inline limit changed.
    """
    assert inline_limit % LIST_PAGE == 0, "an inline limit is whole list pages"
    return f"page={inline_limit // LIST_PAGE + 1}, size={LIST_PAGE}"


def short_list(lines: list[str], *, cap: int = 10) -> list[str]:
    """The first ``cap`` of ``lines``, and a line saying how many were not shown."""
    if len(lines) <= cap:
        return lines
    return [*lines[:cap], f"  … and {len(lines) - cap} more; narrow the name to see them"]


def rest_of(noun: str, *, inlined: int, total: int | None, call: str) -> str:
    """The line beside a ``…Truncated: true`` that says what to do about it.

    A bare flag tells the caller the answer is short and nothing else; this
    puts the count and the call that continues it in the same place, so the
    flag is never the end of the road. ``call`` is written to be pasted.
    """
    have = f"{inlined} of {total} {noun}" if total is not None else f"{inlined} {noun}"
    return f"{have} inlined; the rest: {call}"


def name_candidates(page_body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in page_items(page_body):
        record_id = item.get("id")
        name = item.get("name")
        if isinstance(record_id, str) and record_id:
            out.append({"id": record_id, "name": name if isinstance(name, str) else ""})
    return out


__all__ = [
    "collection_total",
    "collection_truncated",
    "continuation",
    "name_candidates",
    "page_items",
    "rest_of",
    "short_list",
]
