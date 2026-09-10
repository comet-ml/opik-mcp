"""What a project contains, distilled from its activity feed.

"How is my project doing" is usually followed by "and what is in it". One
backend call answers that for every kind at once — experiments, dataset and
test-suite versions, prompt versions, optimization runs, alert events — and it
answers it better than a count per kind would: "the last experiment was
baseline-v4, two days ago" says more than "there are 17 experiments", and
comes with an id to open.

Two properties of the feed shape what can honestly be said from it.

**The per-day trace entry is not a name.** It carries the day's trace count in
the field every other kind uses for a name, so passing it through leaves an
agent reading an object called "5". The summary already reports traces
properly, so the entry is dropped rather than reinterpreted.

**The feed is one stream ordered by date, and it is paged.** The trace roll-up
grows by a row a day, so a project's only experiment can sit behind a year of
trace rows. When the feed is longer than the page we read, a kind's absence is
not evidence that the kind is absent — and the block says so instead of
letting the silence be read as an answer.
"""

from __future__ import annotations

from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.decorations import block

FEED_PAGE: Final = 100
"""The backend's maximum. There are seven kinds and the feed is dominated by
the per-day trace roll-up, so reading as deep as allowed is what gives the
older kinds a chance to appear."""

_TRACE_ROLLUP: Final = "trace_daily"
"""The kind whose ``name`` is a count. Excluded — see the module docstring."""

UI_PAGE: Final[dict[str, str]] = {"optimization": "optimizations"}
"""Kinds this block can hand over to the UI, and the page that opens them.

Of the six kinds the feed reports, one (``experiment``) is readable through
this tool by the id the entry carries, and the rest are not: naming a thing
and then offering no way to reach it is the dead end this closes. Only the
kinds whose UI route takes exactly the id we hold are listed — an experiment's
page is keyed by its *dataset*, which the feed does not give us, and a guessed
link is worse than none.
"""


def _entry(row: dict[str, Any]) -> dict[str, Any] | None:
    """One activity row → ``{name, id, at}``, or ``None`` if it says nothing.

    Only the fields the backend actually sends: the owning resource and the
    author come back absent rather than null, and rendering them as null would
    put two empty fields on every entry. ``at`` keeps the date only — this
    block answers "what is here and how fresh", and the entity's own read
    carries exact times.
    """
    name = row.get("name")
    created = row.get("created_at")
    if not isinstance(name, str) or not name:
        return None
    entry: dict[str, Any] = {"name": name}
    if isinstance(row.get("id"), str):
        entry["id"] = row["id"]
    if isinstance(created, str) and created:
        entry["at"] = created[:10]
    return entry


def distil(body: dict[str, Any]) -> dict[str, Any] | None:
    """The activity page → the freshest entry per kind, or ``None`` if none.

    The feed arrives newest first, so the first row of a kind is its freshest
    and later ones are dropped.

    Entries carry no links: the UI base and the workspace are session facts,
    so the read attaches those afterwards, through the seam that exists for
    exactly that (:func:`read.project_links`).
    """
    raw = body.get("content")
    rows = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []

    block: dict[str, Any] = {}
    for row in rows:
        kind = row.get("type")
        if not isinstance(kind, str) or kind == _TRACE_ROLLUP or kind in block:
            continue
        entry = _entry(row)
        if entry is not None:
            block[kind] = entry
    if not block:
        return None

    total = body.get("total")
    if isinstance(total, int) and total > len(rows):
        block["note"] = (
            f"the {len(rows)} most recent activity records of {total}; a kind missing "
            "here may simply be older than that, not absent"
        )
    return block


async def project_contents(client: OpikReadClient, project_id: str) -> dict[str, Any] | None:
    """The ``contains`` block for a project read, or ``None`` for a quiet project."""

    async def load() -> dict[str, Any] | None:
        return distil(await client.list_project_activities(project_id, size=FEED_PAGE))

    return await block("what this project contains", load)


__all__ = ["FEED_PAGE", "UI_PAGE", "distil", "project_contents"]
