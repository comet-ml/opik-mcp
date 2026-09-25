"""The failure convention for a composite read's optional blocks.

A project read is one primary call and four decorations. The primary must fail
the read — there is no useful answer without the record. A decoration must
never fail it, and must never come back looking like data either: a metric
that could not be loaded arriving as a zero is the one outcome worth guarding
against, because "no traces this week" is advice someone may act on.

The exception list is the load-bearing part. Catching only the typed
``Opik*Error``s left ``httpx.TimeoutException`` and ``httpx.HTTPError``
uncaught, so a single slow decoration killed the whole read — and surfaced as
a raw exception rather than a tool error, so the agent lost the record it
already had *and* got no guidance. That is the likeliest failure of a
five-way fan-out, not an exotic one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx

from opik_mcp.config import Settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikListClient,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.read_list.handler import EntityHandler, PageContext, PageNoteFn, ParentPage
from opik_mcp.read_list.project_scope import resolved_project
from opik_mcp.read_list.ui_links import (
    project_page_url,
    row_link_template,
    view_link_note,
)

logger = logging.getLogger("opik_mcp.read_list.decorations")

BLOCK_ERRORS: Final[tuple[type[BaseException], ...]] = (
    OpikAuthError,
    OpikNotFoundError,
    OpikValidationError,
    OpikServerError,
    # Everything httpx raises for a connection that never answered: timeouts,
    # resets, DNS, TLS. `HTTPError` is the base of both `TimeoutException` and
    # `TransportError`, so this one entry covers the family.
    httpx.HTTPError,
)


def describe(what: str, exc: BaseException) -> str:
    """The text a failed block carries instead of data.

    ``httpx`` exceptions often stringify to nothing at all (a bare
    ``ReadTimeout`` has an empty message), which would leave the agent an
    error field with no error in it — so the class name stands in.
    """
    detail = str(exc).strip() or type(exc).__name__
    return f"Could not load {what}: {detail}"


DEADLINE_SECONDS: Final = 2.5
"""How long a decoration may hold up the answer it decorates.

Generous against the healthy case — the project read's legs measure 130-500 ms
against production — and it exists because one of them does not stay healthy:
``/activities`` answers in ~250 ms for most projects and 5-11 s for some (the
same projects each time, and with a single row, so it is the shape of the data
rather than its volume). A gathered read finishes with its slowest leg, so
without a deadline one backend query turns a half-second answer into an
eight-second one.

The client's own 30 s timeout still governs the primary fetch. This bounds only
what is optional, which is the whole point: an overview that arrives promptly
missing one block beats a complete one the user gave up waiting for.
"""


async def block[T](what: str, load: Callable[[], Awaitable[T]]) -> T | dict[str, Any]:
    """Run one decoration, or return ``{"error": …}`` describing why not.

    Wrapping each leg means a gathered fan-out cannot be brought down by one
    of them: ``asyncio.gather`` without ``return_exceptions`` propagates the
    first failure and discards every sibling's result, so the guard has to sit
    inside each leg rather than around the gather.

    The deadline is read from :data:`DEADLINE_SECONDS` at call time, not bound
    as a default argument — a module constant used as a default is fixed at
    import and cannot be adjusted afterwards, including by a test that means
    to. It was also a parameter for a while; nothing ever passed it.
    """
    limit = DEADLINE_SECONDS
    try:
        async with asyncio.timeout(limit):
            return await load()
    except TimeoutError:
        logger.debug("%s exceeded its %.1fs deadline", what, limit)
        return {
            "error": (
                f"Could not load {what}: the backend took longer than "
                f"{limit:g}s, so it was left out rather than holding up the "
                "rest of the answer. Retry, or ask for it on its own."
            )
        }
    except BLOCK_ERRORS as exc:
        logger.debug("%s failed: %s", what, exc, exc_info=True)
        return {"error": describe(what, exc)}


def _project_of(ctx: PageContext) -> str:
    """The page's project id from what is already in hand, or ``""``.

    A link needs an id and the caller may only have written a name, which is
    the commoner spelling — so a note that read ``ctx.project_id`` alone left
    the usual page linkless.

    Three sources, in cost order, none of which is a call: what the caller
    passed, what the rows carry, and what this listing already resolved to
    reach its own endpoint. There is deliberately no fourth. Turning a name
    into an id would undo the property that makes ``project_name`` as cheap
    as ``project_id`` here, and a decoration does not get to spend a call the
    page itself declined to, so a page whose rows name no project carries no
    link.
    """
    if ctx.project_id:
        return ctx.project_id
    for row in ctx.rows:
        found = row.get("project_id")
        if isinstance(found, str) and found:
            return found
    return resolved_project() or ""


async def _parent_page_note(
    client: OpikListClient, settings: Settings, parent_page: ParentPage, parent_id: str
) -> str | None:
    """The page this listing's parent lives on, or nothing.

    The one link on this server that costs a call. A case is listed under a
    dataset and names no project; the dataset knows its own, so the dataset
    is read. That is what the note hook is handed a client for, and the rule
    it lives under holds: a decoration must never be the reason an answered
    page comes back as an error, so every failure here is silence.

    Silence is also the honest answer for a parent with no project: it has no
    page either, and the parent's own read is where that is explained rather
    than on every page of its cases.
    """
    try:
        parent = await parent_page.fetch(client, parent_id)
    except Exception:
        logger.debug("link note: could not read the %s %r", parent_page.noun, parent_id)
        return None
    project_id = parent.get("project_id") if isinstance(parent, dict) else None
    if not isinstance(project_id, str) or not project_id:
        return None
    subpath = parent_page.subpath
    url = project_page_url(
        settings,
        project_id,
        parent_page.area,
        subpath=f"{parent_id}/{subpath}" if subpath else parent_id,
    )
    if url is None:
        return None
    name = parent.get("name") if isinstance(parent, dict) else None
    named = f"{name!r}" if isinstance(name, str) and name else "its parent"
    return f"Open in Opik: the {parent_page.noun} {named} these belong to — {url}"


def page_note_of(handler: EntityHandler) -> PageNoteFn | None:
    """The note a ``list`` page of this entity ends with, or ``None``.

    The entity's own ``page_note_fn`` when it has one; otherwise the link to
    its parent's page, when it declares a ``parent_page``.
    """
    if handler.page_note_fn is not None:
        return handler.page_note_fn
    parent_page = handler.parent_page
    if parent_page is None:
        return None

    async def note(client: OpikListClient, settings: Settings, ctx: PageContext) -> str | None:
        if ctx.empty or not ctx.parent_id:
            return None
        return await _parent_page_note(client, settings, parent_page, ctx.parent_id)

    return note


def link_note_for(entity_type: str) -> PageNoteFn:
    """The ``page_note_fn`` that tells a page's reader how to open its rows.

    One factory rather than the same hook pasted into each entity module: six
    copies of it differed only in the entity's own name, which is the shape
    that drifts — five get fixed and the sixth keeps the old wording.

    Two kinds of page, and the entity decides which by what it has. A page
    whose rows are addressable gets a template with the row's own columns as
    slots. A page of things that are not addressable at all — a score name is
    a column, a rule is a row, a metric is a chart — gets the page they are
    visible on, named for what it is, so the link does not read as a link to
    the entity.
    """

    async def note(_client: OpikListClient, settings: Settings, ctx: PageContext) -> str | None:
        project_id = _project_of(ctx)
        view = view_link_note(settings, entity_type, project_id, empty=ctx.empty)
        if view is not None:
            return f"Open in Opik: {view['url_opens']} — {view['url']}"
        if ctx.empty:
            # A template addresses a row, and there are none. Returning nothing
            # also lets the generic "why is this page empty" probes run.
            return None
        template = row_link_template(settings, entity_type, project_id)
        if template is None:
            return None
        return (
            f"Open a row in Opik: {template['url_template']} — fill the slots from "
            "the row's own columns. Show it to the user as a link named after the "
            "row, never as a bare URL."
        )

    return note


__all__ = [
    "BLOCK_ERRORS",
    "DEADLINE_SECONDS",
    "block",
    "describe",
    "link_note_for",
    "page_note_of",
]
