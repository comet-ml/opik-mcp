"""What a read/list entity has to declare, and nothing about any of them.

``read`` and ``list`` are one dispatcher each over a table of entities, and
this is the shape of a row in that table. It lives apart from the table so an
entity's own package can state its contract without importing the registry
that collects it, and so the dispatchers can be typed against the contract
rather than against the collection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient

# ``FetchFn`` is widened to ``...`` so project-scoped fetchers (only ``thread``
# today) can accept ``project_id`` / ``project_name`` kwargs. Every other
# fetcher is still ``(client, id)`` and is called positionally; only the
# ``needs_project`` branch in ``read_tool`` passes the extra kwargs.
FetchFn = Callable[..., Awaitable[dict[str, Any]]]
SearchByNameFn = Callable[[OpikReadClient, str], Awaitable[list[dict[str, Any]]]]
ListFn = Callable[..., Awaitable[dict[str, Any]]]
LinkFn = Callable[[Settings, dict[str, Any]], dict[str, str]]


@dataclass(frozen=True)
class ReadWindow:
    """The kwargs an entity's ``fetch_fn`` takes a window in, and their shape.

    ``day_truncated`` means the backend aggregates per whole UTC day, so the
    resolved instants are cut to their date part before being forwarded —
    passing an instant to a day-keyed endpoint silently drops rows.
    """

    start_kwarg: str
    end_kwarg: str
    day_truncated: bool = False


@dataclass(frozen=True)
class PageContext:
    """What a ``list`` page knows about itself, for an entity that has
    something extra to say about it.

    One object rather than six parameters: the fields travel together, and the
    next entity to grow a note will want the same set.
    """

    project_id: str | None = None
    project_name: str | None = None
    empty: bool = False
    status: str | None = None
    windowed: bool = False
    window_end: datetime | None = None


PageNoteFn = Callable[[OpikListClient, Settings, PageContext], Awaitable[str | None]]
RunFn = Callable[..., Awaitable[str]]
ReferenceFn = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class EntityHandler:
    entity_type: str
    fetch_fn: FetchFn
    description: str
    """What this entity is, for whoever reads the registry.

    Nothing advertises it: the tool descriptions in ``server.py`` and the
    payloads of ``schema()`` are written by hand, and no consumer reads this
    field. So it is a comment with a colon in it — keep it to a line or two,
    and never let it be the only place a rule is written down. Two of these
    contradicted the code before anyone noticed, precisely because no output
    ever showed them.
    """
    search_by_name_fn: SearchByNameFn | None = None
    list_fn: ListFn | None = None
    list_extra_fields: tuple[str, ...] = ()
    list_required_kwargs: tuple[str, ...] = ()
    """Entity-specific kwargs ``list_fn`` cannot run without (a parent id).

    ``project_id`` is special: ``project_name`` satisfies it too, since every
    project-scoped list accepts either.
    """
    list_optional_kwargs: tuple[str, ...] = ()
    """Entity-specific kwargs ``list_fn`` accepts but does not require.

    The ``list`` tool forwards a kwarg only when the entity declares it here
    or in ``list_required_kwargs``; anything else the caller passed is dropped
    so a confused call degrades to a plain list instead of a client TypeError.
    """
    read_window: ReadWindow | None = None
    """How ``fetch_fn`` takes a ``since``/``until`` window, or ``None`` for the
    entities that take none (most of them).

    The two entities that do take one want it in different shapes — the
    Diagnostics endpoints aggregate per UTC report day and want dates, a
    project's metrics want instants — so the shape is declared here rather than
    assumed by the read tool."""
    link_fn: LinkFn | None = None
    """Optional: UI links to attach to the fetched composite before it is rendered.

    Called by ``read`` with the session's ``Settings`` and the fetched data;
    returns extra top-level fields (e.g. ``url``) and must not mutate the
    data. Fetchers cannot do this themselves — they see a client, not
    settings — and the UI base/workspace are session facts, not entity
    facts. A fetcher may stash inputs for the link under underscore-prefixed
    keys; ``read`` strips those before rendering. Return ``{}`` when Opik's
    URL or the workspace cannot be known: no link beats a wrong one.
    """
    run_fn: RunFn | None = None
    """Optional: the entity answers ``list`` on its own, whole.

    A time series is not a collection, so page, size, sort, the shared table
    and the per-entity filter fields do not apply to it. Declaring the runner
    here rather than branching on the entity type in the list tool keeps the
    collection path free of a case it cannot serve, and keeps the tool from
    naming any entity. The runner is called with the tool's arguments and
    returns the finished answer.
    """
    reference_fn: ReferenceFn | None = None
    """Optional: what ``schema('list.<entity>')`` answers for this entity.

    The default is built from the OQL field table. An entity whose reference
    is a different shape (the metric catalog, not a field list) supplies it,
    beside the data it documents.
    """
    page_note_fn: PageNoteFn | None = None
    """Optional: a sentence or two to append to a ``list`` page of this entity.

    The counterpart of ``link_fn`` for lists. ``list`` calls it with the
    client, the session's ``Settings`` and a :class:`PageContext`, for an empty
    page and a full one alike, and appends whatever comes back. Return ``None``
    to leave the page as it was: a note decorates an answer the caller already
    has, so a failed lookup inside the hook must never turn an answered list
    into an error.

    It lives here so that what an entity says about its own pages is one
    registry entry rather than a branch on ``entity_type`` inside the list
    tool.
    """

    @property
    def lists(self) -> bool:
        """Can ``list`` answer for this entity, by either route?

        Either the collection path drives ``list_fn``, or ``run_fn`` answers
        the whole call. Asking this rather than ``list_fn is not None`` is
        what let the ``delegated_elsewhere`` sentinel go: it existed only to
        make that test true for the one entity that does not use the
        collection path, which meant the handler declared a list function it
        never calls.
        """
        return self.list_fn is not None or self.run_fn is not None

    list_has_name: bool = True
    """False for entities whose records carry no ``name`` (thread) — the table
    then starts at ``id`` instead of rendering an always-empty name column."""
    list_has_id: bool = True
    """False for entities the backend addresses by name alone (score_name) —
    the mirror of ``list_has_name``, so the table drops the id column rather
    than printing a column of nothing on every row."""
    no_window_reason: str | None = None
    """Why this entity takes no ``since``/``until``, where the general answer
    would mislead.

    The default refusal says only trace, span and thread take a window, which
    reads as our limitation. For an experiment it is the backend's shape: the
    endpoint has no such parameter. That is a fact about the entity, so the
    entity says it, the same way it declares the window it *does* take.
    """
    list_footer: str | None = None
    """One line appended under a non-empty listing: a caveat the rows cannot
    carry themselves.

    Only for something an agent would otherwise get wrong from the table alone
    — a score name does not say whether it was attached to a trace, a span or
    a thread, and filtering traces by a thread's score returns nothing that
    looks like good news. The entity description is the wrong home for it:
    nothing surfaces those to the agent."""
    id_only: bool = False
    """True if the entity is addressed only by UUID (no name lookup).

    The ``read`` tool uses this to skip the name-lookup branch entirely —
    saves one round-trip on every non-UUID input for traces/spans/etc.
    """
    needs_project: bool = False
    """True if ``fetch_fn`` needs project scope (thread, agent_insights_issue).

    A thread id is unique only within a project, and the agent-insights
    endpoints require ``project_id`` as a query parameter, so ``read`` must
    pass ``project_id`` / ``project_name`` into the fetcher. The read tool
    branches on this flag: when set it calls ``fetch_fn(client, id,
    project_id=…, project_name=…)`` and requires one of them; otherwise it
    calls ``fetch_fn(client, id)`` exactly as before. Orthogonal to ``id_only``.
    """


__all__ = [
    "EntityHandler",
    "FetchFn",
    "LinkFn",
    "ListFn",
    "PageContext",
    "PageNoteFn",
    "ReadWindow",
    "ReferenceFn",
    "RunFn",
    "SearchByNameFn",
]
