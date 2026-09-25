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
from opik_mcp.read_list.ui_links import ProjectArea

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
    parent_id: str | None = None
    """The id this listing is scoped by, where that is not a project.

    A case is listed under a dataset and a version under a prompt, and
    neither row says which project the parent belongs to — so a note that
    wants to link the parent's page has to be told what the parent is. The
    list tool fills it from whichever of the entity's required kwargs is a
    parent id.
    """
    empty: bool = False
    status: str | None = None
    windowed: bool = False
    window_end: datetime | None = None
    page: int = 1
    total: int = 0
    """What the backend said matched, and which slice of it was asked for.

    ``empty`` alone cannot tell "nothing matched" from "you paged past the
    last page": both arrive with no rows. A note that reads the first as the
    second states a falsehood — the rows do match, they are on page one — and
    that is the exact failure these notes exist to prevent.
    """
    filtered: bool = False
    """Did the caller write a ``filters`` clause, as opposed to narrowing by
    name or not at all? Advice about filter fields is an answer to a question
    only a filtering caller asked."""
    sort_field: str | None = None
    rows: tuple[dict[str, Any], ...] = ()
    """The page as the backend sent it, and the field it was ordered by.

    ``empty`` is the fact most notes need. These are for a note that reads
    values off the page — how far apart the first two rows are on the field
    they were sorted by, say. Only the entity knows which of its fields that
    is and how the value sits in its record, so the tool hands over the rows
    and the name and decides nothing."""


PageNoteFn = Callable[[OpikListClient, Settings, PageContext], Awaitable[str | None]]
LinkRowFn = Callable[[Settings, dict[str, Any]], str | None]
RunFn = Callable[..., Awaitable[str]]
ReferenceFn = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class ListProjection:
    """The columns one ``list`` page shows, decided from the page itself.

    Most entities declare their columns once, in ``list_extra_fields``: a
    trace always has a ``start_time``. A dataset item does not have fixed
    fields — its payload is a ``data`` map whose keys the user chose when they
    built the dataset — so the columns can only be known once the page is in
    hand. This is what an entity's ``list_projection_fn`` returns for it.
    """

    columns: tuple[str, ...]
    """Column names after the entity's base columns (id, name), in order. A
    dotted name resolves into a nested container the way a filter field does
    (``data.question`` is ``item["data"]["question"]``)."""
    cell_limit: int
    """Characters a value may take before it is cut. The table states every
    cut it makes under the rows, so a short value is never mistaken for the
    whole one."""
    note: str | None = None
    """A line under the table saying where the columns came from and which
    were left out. Required whenever ``columns`` is not everything the page
    had: a silent cut is a wrong answer the caller cannot suspect."""
    cut_hint: str | None = None
    """How the caller lifts the cut, appended to the cut line."""


ProjectionFn = Callable[[list[dict[str, Any]]], ListProjection]
RowFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Vocabulary:
    """One field table a ``list`` call checks its filters and sort against.

    Usually an entity has one, named after it. An entity whose two calls hit
    two backend endpoints has one per endpoint, and ``list_vocabulary`` names
    the one its collection path uses.
    """

    name: str
    sort_fields: tuple[str, ...] = ()
    """What the backend orders by, from its ``*SortingFactory``. An entry
    ending in ``.*`` is a dynamic prefix: ``feedback_scores.<name>``."""
    unsortable_why: str | None = None
    """Why ``sort_fields`` is empty, in the refusal's voice, for an endpoint
    that orders by nothing: whose limit it is and what orders the same rows."""


@dataclass(frozen=True)
class ParentPage:
    """The page a listing links to when its rows have none and their parent does.

    A case is listed under a dataset and a version under a prompt. Neither row
    names a project, so the list page reads the parent (``fetch``) for its
    ``project_id`` and links ``<area>/<parent id>[/<subpath>]`` under it.
    """

    fetch: FetchFn
    area: ProjectArea
    subpath: str
    noun: str
    """What the parent is called in the note: "the dataset 'cases' these belong to"."""


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
    list_projection_fn: ProjectionFn | None = None
    """Optional: choose the page's columns from the page, instead of from
    ``list_extra_fields``.

    For an entity whose record has no fixed fields to name up front. Called
    with the page's rows, never with an empty page; returns a
    :class:`ListProjection`. When set, ``list_extra_fields`` is not read.
    """
    list_link_fn: LinkRowFn | None = None
    """Optional: a ``url`` for each row, for a listing whose rows cannot share one.

    The cheap answer for a project-scoped page is one template with the row's
    own columns as slots, and most listings take it. This is for the page that
    cannot: an experiment's address needs its project *and* its dataset, both
    of which vary down a workspace-wide page, so one template would have
    nothing constant to be built from.

    Unlike ``list_row_fn`` this is handed the session's ``Settings``, because a
    url is a fact about the session — where Opik lives, which workspace — and
    not about the record. Return ``None`` for a row that cannot be addressed;
    the column then simply has no cell there.
    """
    list_row_fn: RowFn | None = None
    """Optional: derive the columns a record does not carry from the ones it
    does, before the page is projected and rendered.

    Some facts arrive split across fields and read as one cell: an experiment
    reports ``passed_count`` and ``total_count`` separately, and what the
    caller wants to see is how many assertion runs passed. Others arrive
    nested in a shape the generic dotted lookup cannot name well. Deriving
    them here keeps the list tool free of any entity's field names — the one
    derivation it does know about, ``error_type`` out of the error container,
    is the exception this exists to stop multiplying.

    Returns a new mapping; the page the backend sent is left alone for
    everything else that reads it.
    """
    list_identity_fields: tuple[str, ...] = ()
    """Columns a projected row keeps beyond its id, even unasked for.

    A projection is the caller narrowing the answer, and the one thing they
    must not be able to narrow away is the handle that opens the next level:
    a case's trace, a span's id, the prompt version an experiment ran. Without
    it a row is a fact with no way back to the record behind it, which is the
    shape of answer this server exists not to give.

    Named per entity because only the entity knows which of its columns is
    that handle. A column no row on the page fills is not added — an empty
    column is a field the records lack, and inventing one would undo the point
    of :mod:`opik_mcp.read_list.projection`'s naming rule.
    """
    vocabularies: tuple[Vocabulary, ...] = ()
    """The field tables this entity's ``list`` validates against: its own,
    named after it, and any other one ``list_vocabulary`` names."""
    list_vocabulary: str | None = None
    """The OQL and sort vocabulary the collection path validates against, when
    it is not the entity's own name.

    An entity whose two calls hit two backend endpoints filters on two sets of
    fields: a dataset item listed under its dataset is filtered on the case
    (``data.<key>``, ``full_data``, ``trace_id``), and the same item listed
    with experiments attached is filtered on the runs (``duration``,
    ``output``, ``feedback_scores``). The runner owns the second and names it
    itself; this is how the collection path is told about the first. ``None``
    means the entity's name is its vocabulary, which is the usual case.
    """
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

    parent_page: ParentPage | None = None
    """Optional: link a ``list`` page to its parent's page, for rows with no page.

    Read by :func:`opik_mcp.read_list.decorations.page_note_of` when the entity
    declares no ``page_note_fn`` of its own. It costs one call, spent only on a
    non-empty page, and any failure leaves the page without the note.
    """

    run_verb: str = "list"
    """What the runner was doing, for the error an upstream failure becomes.

    "Failed to chart dataset_item" is what a comparison used to say when
    opik-backend refused it: the metric runner arrived first and its verb was
    written into the shared path. The word belongs to whoever owns the call.
    """

    run_timeout_hint: str | None = None
    """What to try when the runner's call times out; ``None`` for the default.

    The metric's advice (narrow the window, widen the interval) is nonsense
    for a comparison, which has neither, so the hint travels with the runner
    rather than with the tool.
    """

    run_when_kwargs: tuple[str, ...] = ()
    """Tool arguments that hand the call to ``run_fn``; empty means always.

    An entity can answer one question through the collection path and another
    through its runner — a dataset's cases are a collection, the same cases with
    two experiments' runs attached are not. Naming the arguments that switch
    between them here keeps the ``list`` tool from knowing which entity has
    two questions, and lets a test pin the choice.
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
    "ListProjection",
    "PageContext",
    "PageNoteFn",
    "ParentPage",
    "ProjectionFn",
    "ReadWindow",
    "ReferenceFn",
    "RowFn",
    "RunFn",
    "SearchByNameFn",
    "Vocabulary",
]
