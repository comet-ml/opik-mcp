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


def name_candidates(page_body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in page_items(page_body):
        record_id = item.get("id")
        name = item.get("name")
        if isinstance(record_id, str) and record_id:
            out.append({"id": record_id, "name": name if isinstance(name, str) else ""})
    return out


__all__ = ["collection_truncated", "name_candidates", "page_items"]
