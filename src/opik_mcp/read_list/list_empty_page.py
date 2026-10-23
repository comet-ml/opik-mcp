"""What an empty ``list`` page says, and the one-row probes that let it say
why it is empty.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from opik_mcp.client.protocols import OpikListClient
from opik_mcp.config import Settings
from opik_mcp.read_list.decorations import page_note_of
from opik_mcp.read_list.handler import EntityHandler, ListFn, ListKwargs, PageContext, Vocabulary
from opik_mcp.read_list.oql import split_param_clauses
from opik_mcp.read_list.oql_fields import SDK_SOURCE_CLAUSE
from opik_mcp.read_list.project_scope import project_rows
from opik_mcp.read_list.window import parse_instant, to_minute

# The list tool's logger: a probe runs inside a list call.
logger = logging.getLogger("opik_mcp.read_list.list")


def _source_hint(entity_type: str, source_values: tuple[str, ...], hidden: int) -> str:
    """What the ``sdk`` default hid, in numbers, and how to see it.

    Names every source the backend writes, ``optimization`` included: the
    optimizer's traces were the ones a caller went looking for and were not
    told about.
    """
    named = [v for v in source_values if v not in ("sdk", "unknown")]
    choices = ", ".join(f'"{v}"' for v in named[:-1]) + f' or "{named[-1]}"'
    return (
        f"{hidden} {entity_type}{'s' if hidden != 1 else ''} match without the default "
        'source = "sdk", which is all that is listed unless you name a source. Add '
        f"source = {choices} to filters to see them."
    )


def without_default(
    vocabulary: Vocabulary,
    opik: OpikListClient,
    list_fn: ListFn,
    list_kwargs: ListKwargs,
    clauses: list[dict[str, str]],
) -> Callable[[], Awaitable[int | None]]:
    """The same listing again, one row wide, with the ``sdk`` default lifted.

    An empty page under the default used to earn a hint only when the default
    was the sole clause — the guess being that a caller's own filter was the
    likelier reason. Driving the tool: ``experiment_id = "…"`` on an
    experiment with twenty traces got "No traces found" and no hint, because
    the guess was wrong and there was no way to know. So the hint is earned by
    asking: it fires when the widened count is non-zero, whatever the caller
    filtered on, and stays silent on a project that is genuinely empty.

    Returns ``None`` when the probe cannot answer; a note decorates an answer
    the caller already has, and must never turn it into an error.
    """

    return probe_count(
        vocabulary, opik, list_fn, list_kwargs, [c for c in clauses if c != SDK_SOURCE_CLAUSE]
    )


def without_filters(
    vocabulary: Vocabulary,
    opik: OpikListClient,
    list_fn: ListFn,
    list_kwargs: ListKwargs,
    clauses: list[dict[str, str]],
) -> Callable[[], Awaitable[int | None]]:
    """The same listing again, one row wide, with the caller's filters lifted.

    Keeps the ``sdk`` default when the page applied it, so the count is of the
    rows the caller would otherwise have seen.
    """
    return probe_count(
        vocabulary, opik, list_fn, list_kwargs, [c for c in clauses if c == SDK_SOURCE_CLAUSE]
    )


def probe_count(
    vocabulary: Vocabulary,
    opik: OpikListClient,
    list_fn: ListFn,
    list_kwargs: ListKwargs,
    clauses: list[dict[str, str]],
) -> Callable[[], Awaitable[int | None]]:
    """One-row count of ``clauses``, or ``None`` when it cannot be had."""

    async def count() -> int | None:
        probe: ListKwargs = {**list_kwargs, "page": 1, "size": 1}
        probe.pop("filters", None)
        try:
            # The same split the page went through: a clause that is a query
            # parameter must not turn back into a filter entry on the probe.
            sent, params = split_param_clauses(vocabulary, clauses)
            probe.update(params)
            if sent:
                probe["filters"] = json.dumps(sent, separators=(",", ":"))
            body = await list_fn(opik, **probe)
        except Exception:
            logger.debug("empty-page probe failed", exc_info=True)
            return None
        found = body.get("total")
        return found if isinstance(found, int) else None

    return count


async def empty_message(
    opik: OpikListClient,
    handler: EntityHandler,
    *,
    name: str | None,
    from_time: str | None,
    widened: Callable[[], Awaitable[int | None]] | None,
    unfiltered: Callable[[], Awaitable[int | None]] | None,
    unnamed: Callable[[], Awaitable[int | None]] | None,
    settings: Settings,
    page_ctx: PageContext,
    source_values: tuple[str, ...],
) -> str:
    """The empty-page reply, with the one hint that explains it when we can.

    Three cases seen live look identical without help: a project holding only
    experiment traces under the ``source = "sdk"`` default, a window that
    starts after the project's last trace, and a filter that matched none of a
    scope that is not itself empty. Each costs one extra call, spent only on an
    empty page.

    An entity whose empty page has its own ambiguity (a Diagnostics issue
    list: never enabled, off, unscanned, or genuinely clean) explains itself
    through its registry ``page_note_fn``, and its answer replaces the probes
    below — it knows something they cannot work out.

    A note of ``None`` is not that: it means the entity had nothing to add to
    *this* page, so the probes still run. The distinction matters now that a
    note can be about something other than emptiness — a link for opening a
    row has nothing to say about a page with no rows, and silencing the probes
    would have been an odd way to say so.
    """
    entity_type = handler.entity_type
    project_id, project_name = page_ctx.project_id, page_ctx.project_name
    empty = f"No {entity_type}s matching {name!r} found." if name else f"No {entity_type}s found."
    page_note = page_note_of(handler)
    if page_note is not None:
        note = await page_note(opik, settings, page_ctx)
        if note:
            return f"{empty} {note}"

    async def scoped() -> str:
        """What the narrowing matched none of, when nothing else explains it."""
        probe, lifted = (
            (unnamed, "that name") if unnamed is not None else (unfiltered, "your filter")
        )
        found = await probe() if probe is not None else None
        if not found:
            return empty
        plural = "s" if found != 1 else ""
        return (
            f"{empty} Without {lifted} this listing has {found} "
            f"{entity_type}{plural}; none of them match it."
        )

    async def hinted() -> str:
        hidden = await widened() if widened is not None else None
        return (
            f"{empty} {_source_hint(entity_type, source_values, hidden)}"
            if hidden
            else await scoped()
        )

    if from_time is None:
        return await hinted()

    rows = await project_rows(opik, name=project_name)
    match = [
        p
        for p in rows
        if (p.get("name") == project_name if project_name else p.get("id") == project_id)
    ]
    if not match:
        return empty
    last = match[0].get("last_updated_trace_at")
    if not last:
        return f"{empty} This project has no traces yet."
    last_dt, start_dt = parse_instant(str(last)), parse_instant(from_time)
    if last_dt is None or start_dt is None:
        return empty
    if last_dt < start_dt:
        return f"{empty} Last trace in this project: {to_minute(last_dt)}, before your window."
    # Traffic exists inside the window, so the default source is what hid it.
    return await hinted()
