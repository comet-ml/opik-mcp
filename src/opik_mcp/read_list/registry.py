"""Entity registry — the single source of truth for ``read`` / ``list``.

One entry per entity type. Each entry knows:

- how to ``fetch`` a singleton by id (always present)
- how to ``search_by_name`` (optional — only for entities the backend
  exposes a name-filtered index for)
- how to ``list`` (optional — only for paginated workspace collections)

Composite entities (``trace`` = trace + spans tree; ``prompt`` = prompt +
versions) hide the multi-call fan-out inside ``fetch_fn`` and return a
single composite dict. Compression defaults to the generic FULL/MEDIUM
pipeline in ``compression.py``; entities that need a custom skeleton
(only ``trace`` today) supply ``compress_fn``.

Scope for Phase 1 covers the Opik entities the agent surface reads today
(trace, span, project, dataset, experiment, prompt, test_suite, …), plus
the list-fns we already had REST methods for. The registry is structured
so adding a new entity is a one-entry diff — fetch, list, search-by-name
plug into the existing dispatchers without further code changes.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.compression import (
    TOKEN_FULL_THRESHOLD,
    TOKEN_SKELETON_THRESHOLD,
    CompressionTier,
    compact_json,
    estimate_tokens,
    truncate_strings,
)
from opik_mcp.read_list.compression import (
    compress as generic_compress,
)
from opik_mcp.read_list.diagnostics import issue_page_note
from opik_mcp.read_list.project_read import fetch_project, project_links
from opik_mcp.read_list.project_scope import require_project_id, scope_of
from opik_mcp.read_list.project_summary import WINDOW_DAYS
from opik_mcp.read_list.ui_links import project_page_url

# Inline caps for composite reads — match the previous resources.py
# constants so cache shapes stay stable for any in-flight integration.
SPANS_INLINE_LIMIT = 200
VERSIONS_INLINE_LIMIT = 100
MESSAGES_INLINE_LIMIT = 200

# ``FetchFn`` is widened to ``...`` so project-scoped fetchers (only ``thread``
# today) can accept ``project_id`` / ``project_name`` kwargs. Every other
# fetcher is still ``(client, id)`` and is called positionally; only the
# ``needs_project`` branch in ``read_tool`` passes the extra kwargs.
FetchFn = Callable[..., Awaitable[dict[str, Any]]]
SearchByNameFn = Callable[[OpikReadClient, str], Awaitable[list[dict[str, Any]]]]
ListFn = Callable[..., Awaitable[dict[str, Any]]]
CompressFn = Callable[[dict[str, Any], int | None], tuple[str, CompressionTier]]
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
    """Optional: UI links to attach to the fetched composite before compression.

    Called by ``read`` with the session's ``Settings`` and the fetched data;
    returns extra top-level fields (e.g. ``url``) and must not mutate the
    data. Fetchers cannot do this themselves — they see a client, not
    settings — and the UI base/workspace are session facts, not entity
    facts. A fetcher may stash inputs for the link under underscore-prefixed
    keys; ``read`` strips those before compression. Return ``{}`` when Opik's
    URL or the workspace cannot be known: no link beats a wrong one.
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
    list_has_name: bool = True
    """False for entities whose records carry no ``name`` (thread) — the table
    then starts at ``id`` instead of rendering an always-empty name column."""
    list_has_id: bool = True
    """False for entities the backend addresses by name alone (score_name) —
    the mirror of ``list_has_name``, so the table drops the id column rather
    than printing a column of nothing on every row."""
    list_footer: str | None = None
    """One line appended under a non-empty listing: a caveat the rows cannot
    carry themselves.

    Only for something an agent would otherwise get wrong from the table alone
    — a score name does not say whether it was attached to a trace, a span or
    a thread, and filtering traces by a thread's score returns nothing that
    looks like good news. The entity description is the wrong home for it:
    nothing surfaces those to the agent."""
    compress_fn: CompressFn | None = None
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


# --- shared helpers ------------------------------------------------------- #


def _content(page_body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = page_body.get("content") or []
    return [it for it in raw if isinstance(it, dict)]


def _is_truncated(page_body: dict[str, Any], *, inlined: int, limit: int) -> bool:
    """Did the embedded collection get capped — by either us or the backend?

    Ported verbatim from the old resources.py (the three-signal rule was
    well-tested there and we want identical behavior).
    """
    total_raw = page_body.get("total")
    if isinstance(total_raw, int) and total_raw >= 0:
        return total_raw > inlined
    size_raw = page_body.get("size")
    if isinstance(size_raw, int) and size_raw > 0 and inlined >= size_raw:
        return True
    return inlined >= limit


def _candidates(page_body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in _content(page_body):
        record_id = item.get("id")
        name = item.get("name")
        if isinstance(record_id, str) and record_id:
            out.append({"id": record_id, "name": name if isinstance(name, str) else ""})
    return out


# --- fetchers ------------------------------------------------------------- #


async def _fetch_trace(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    """Trace + inlined spans (up to ``SPANS_INLINE_LIMIT``).

    The spans index in opik-backend is sharded by project, so the second
    call needs the trace's ``project_id``. A trace without project_id is
    anomalous — return an empty spans list rather than failing the read.
    """
    trace = await client.get_trace(entity_id)
    project_id = trace.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return {"trace": trace, "spans": [], "spansTruncated": False}
    try:
        spans_page = await client.list_spans(
            trace_id=entity_id,
            project_id=project_id,
            page=1,
            size=SPANS_INLINE_LIMIT,
        )
    except Exception:
        return {"trace": trace, "spans": [], "spansTruncated": False}
    spans = _content(spans_page)
    truncated = _is_truncated(spans_page, inlined=len(spans), limit=SPANS_INLINE_LIMIT)
    return {"trace": trace, "spans": spans, "spansTruncated": truncated}


async def _fetch_span(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_span(entity_id)


async def _fetch_test_suite(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_test_suite(entity_id)


async def _fetch_experiment(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_experiment(entity_id)


async def _fetch_prompt(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    """Prompt + full version list (up to ``VERSIONS_INLINE_LIMIT``)."""
    prompt = await client.get_prompt(entity_id)
    try:
        versions_page = await client.list_prompt_versions(
            entity_id, page=1, size=VERSIONS_INLINE_LIMIT
        )
    except Exception:
        return {"prompt": prompt, "versions": [], "versionsTruncated": False}
    versions = _content(versions_page)
    truncated = _is_truncated(versions_page, inlined=len(versions), limit=VERSIONS_INLINE_LIMIT)
    return {"prompt": prompt, "versions": versions, "versionsTruncated": truncated}


def _thread_messages(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project each trace to one conversation turn, sorted by ``start_time`` asc.

    Ascending order is conversation order. Each turn keeps a ``trace_id`` so the
    agent can ``read('trace', id)`` to drill into spans/metadata.
    """
    ordered = sorted(traces, key=lambda t: t.get("start_time") or "")
    messages: list[dict[str, Any]] = []
    for t in ordered:
        msg: dict[str, Any] = {
            "trace_id": t.get("id"),
            "name": t.get("name"),
            "input": t.get("input"),
            "output": t.get("output"),
            "start_time": t.get("start_time"),
            "end_time": t.get("end_time"),
            "duration": t.get("duration"),
            "usage": t.get("usage"),
            "total_estimated_cost": t.get("total_estimated_cost"),
            "feedback_scores": t.get("feedback_scores"),
        }
        error_info = t.get("error_info")
        if error_info is not None:
            msg["error_info"] = error_info
        messages.append(msg)
    return messages


async def _fetch_thread(
    client: OpikReadClient,
    entity_id: str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Thread metadata + its messages (traces filtered by ``thread_id``).

    Two project-scoped calls reproduce the UI's Thread panel: ``get_thread``
    for metadata, then ``list_traces`` filtered by ``thread_id`` for the turns.
    Mirrors ``_fetch_trace``'s defensive inline: if the messages call fails,
    return the metadata with an empty messages list rather than failing the
    whole read.
    """
    thread = await client.get_thread(entity_id, project_id=project_id, project_name=project_name)
    filters = json.dumps([{"field": "thread_id", "operator": "=", "value": entity_id}])
    try:
        traces_page = await client.list_traces(
            project_id=project_id,
            project_name=project_name,
            filters=filters,
            page=1,
            size=MESSAGES_INLINE_LIMIT,
        )
    except Exception:
        # Unlike a trace's spans (secondary), messages ARE a thread's primary
        # payload — so an empty list here must NOT read as "no messages" when
        # the metadata says otherwise. Signal the load failure explicitly.
        return {
            "thread": thread,
            "messages": [],
            "messagesTruncated": False,
            "messagesError": "Failed to load thread messages; retry or read the traces directly.",
        }
    traces = _content(traces_page)
    truncated = _is_truncated(traces_page, inlined=len(traces), limit=MESSAGES_INLINE_LIMIT)
    return {
        "thread": thread,
        "messages": _thread_messages(traces),
        "messagesTruncated": truncated,
    }


def _example_trace_ids(details: list[dict[str, Any]]) -> list[str]:
    """Deduplicated union of each per-day row's ``metadata.example_trace_ids``.

    First-seen order over the rows as the backend returns them (ascending
    report day), which is what the Diagnostics page's affected-traces sample
    shows. ``metadata`` is free-form JSON written by the Diagnostics job — a
    row without it, or with a non-object value, contributes nothing rather
    than failing the read.
    """
    seen: dict[str, None] = {}
    for row in details:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        ids = metadata.get("example_trace_ids")
        if not isinstance(ids, list):
            continue
        for trace_id in ids:
            if isinstance(trace_id, str) and trace_id:
                seen.setdefault(trace_id, None)
    return list(seen)


async def _fetch_agent_insights_issue(
    client: OpikReadClient,
    entity_id: str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Diagnostics issue + deduped example trace ids + per-day breakdown.

    One backend call. Returns ``{issue, example_trace_ids, details}`` plus a
    private ``_project_id`` (see below): the issue record without its
    ``details`` array, the trace ids the agent can open with
    ``read('trace', id)``, and the per-day rows unchanged. Trace bodies are
    deliberately not inlined — that would turn one read into N+1 calls and
    blow the default budget on any issue with several examples.
    """
    # The agent-insights endpoints take project_id only; resolve the name here
    # so the read contract stays "project_id or project_name" for every
    # project-scoped entity. An explicit project_id always wins.
    project_id = await require_project_id(
        client,
        project_id=project_id,
        project_name=project_name,
        caller="read('agent_insights_issue')",
    )
    body = await client.get_agent_insights_issue(
        entity_id, project_id=project_id, from_date=from_date, to_date=to_date
    )
    details_raw = body.get("details")
    details = (
        [row for row in details_raw if isinstance(row, dict)]
        if isinstance(details_raw, list)
        else []
    )
    issue = {key: value for key, value in body.items() if key != "details"}
    return {
        "issue": issue,
        # The link_fn needs the project the issue was read under; the backend
        # record does not carry it. Underscore-prefixed keys are stripped by
        # the read tool after links are attached, so it never reaches the agent.
        "_project_id": project_id,
        "example_trace_ids": _example_trace_ids(details),
        "details": details,
    }


def _issue_links(settings: Settings, data: dict[str, Any]) -> dict[str, str]:
    """The issue's Diagnostics page (open or resolved view, by status) and a
    template for deep-linking any of its example traces — the two links the
    diagnose skill has to hand the user."""
    project_id = data.get("_project_id")
    issue = data.get("issue") or {}
    issue_id = issue.get("id")
    if not isinstance(project_id, str) or not isinstance(issue_id, str):
        return {}
    view = "diagnostics" if issue.get("status") == "open" else "diagnostics/resolved"
    page = project_page_url(settings, project_id, f"{view}?issue={issue_id}")
    traces = project_page_url(settings, project_id, "logs?trace={trace_id}")
    if page is None or traces is None:
        return {}
    return {"url": page, "trace_url_template": traces}


async def _unsupported_fetch(_client: OpikReadClient, _entity_id: str) -> dict[str, Any]:
    """Sentinel for list-only entities. The read tool raises before calling this."""
    raise NotImplementedError(
        "This entity is list-only — use list() with the parent id, or read the parent entity."
    )


async def _delegated_elsewhere(_client: OpikListClient, **_kw: Any) -> dict[str, Any]:
    """Sentinel for an entity the list tool hands off before reaching the registry."""
    raise NotImplementedError(
        "This entity is handled by its own runner; the list tool delegates before here."
    )


# --- search-by-name (only entities with a name-filtered list endpoint) --- #


async def _search_project(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return _candidates(await client.list_projects(name=name, size=5))


async def _search_experiment(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return _candidates(await client.list_experiments(name=name, size=5))


async def _search_prompt(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return _candidates(await client.list_prompts(name=name, size=5))


async def _search_test_suite(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return _candidates(await client.list_test_suites(name=name, size=5))


# --- list fns ------------------------------------------------------------- #


async def _list_projects(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_projects(**kw)


async def _list_score_names(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    """A project's feedback score names, as a page the list tool can render.

    The endpoint answers ``{scores: [{name}]}`` rather than the Spring page
    envelope every other listable endpoint uses, and it takes no paging — the
    query is a ``distinct name`` with no ``LIMIT``. So the slice is ours to
    make, and it has to be made: returning every name with ``total`` set to
    every name left the table's own footer promising a "page 2" that returned
    the same rows again.
    """
    project_id = await scope_of(client, kw, caller="list('score_name')")
    # Not `project_vocabulary`'s fetcher: that one returns names, and the
    # table renders rows. Same endpoint, two honest shapes.
    body = await client.list_project_score_names(project_id)
    raw = body.get("scores")
    names = (
        [row for row in raw if isinstance(row, dict) and row.get("name")]
        if isinstance(raw, list)
        else []
    )
    page = max(1, int(kw.get("page") or 1))
    size = max(1, int(kw.get("size") or 1))
    start = (page - 1) * size
    window = names[start : start + size]
    return {"content": window, "page": page, "size": len(window), "total": len(names)}


async def _list_online_rules(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    project_id = await scope_of(client, kw, caller="list('online_rule')")
    return await client.list_automation_rules(
        project_id=project_id, page=kw.get("page", 1), size=kw.get("size", 10)
    )


async def _list_experiments(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_experiments(**kw)


async def _list_prompts(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_prompts(**kw)


async def _list_test_suites(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_test_suites(**kw)


async def _list_traces(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Trace listing is project-scoped — ``list`` tool enforces project_id
    # presence via ``list_required_kwargs``. ``name`` filtering on traces
    # isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_traces(**kw)


async def _list_test_suite_items(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # opik-backend's items endpoint is ``/datasets/{id}/items`` — the suite id
    # is in the path, not a query param. Pull it out before forwarding.
    suite_id = kw.pop("test_suite_id", None)
    if not suite_id:
        raise ValueError("list test_suite_item requires test_suite_id")
    kw.pop("name", None)
    return await client.list_test_suite_items(suite_id, **kw)


async def _list_prompt_versions(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    prompt_id = kw.pop("prompt_id", None)
    if not prompt_id:
        raise ValueError("list prompt_version requires prompt_id")
    kw.pop("name", None)
    return await client.list_prompt_versions(prompt_id, **kw)


async def _list_threads(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Thread listing is project-scoped — the ``list`` tool enforces project_id
    # via ``list_required_kwargs``. ``name`` filtering on threads isn't
    # supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_threads(**kw)


async def _list_spans(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Project-wide span search: no ``trace_id`` — that scoping (and ``type``)
    # is expressed in OQL (``trace_id = "…"``, ``type = "llm"``). ``name``
    # filtering isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_spans(**kw)


async def _list_agent_insights_issues(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # "What is broken" means open issues, so that is the default; the caller
    # asks for resolved/closed explicitly. No name filter exists on the backend.
    # No ``sorting`` is sent: the backend's default (last seen, then total
    # occurrences) is the Diagnostics page's ranking.
    kw.pop("name", None)
    kw.setdefault("status", "open")
    # The backend takes project_id only. The list tool lets project_name
    # satisfy the project requirement (as for trace/thread), so resolve it
    # here; an explicit project_id wins and skips the lookup. Not `scope_of`
    # like its two neighbours: the name has to leave ``kw`` as well, since
    # what remains is forwarded to a client method that has no such parameter.
    kw["project_id"] = await require_project_id(
        client,
        project_id=kw.get("project_id"),
        project_name=kw.pop("project_name", None),
        caller="list('agent_insights_issue')",
    )
    return await client.list_agent_insights_issues(**kw)


# --- trace skeleton compression ------------------------------------------ #


def _compress_trace(data: dict[str, Any], max_tokens: int | None) -> tuple[str, CompressionTier]:
    """Trace+spans: FULL → MEDIUM (truncated strings) → SKELETON (span tree only).

    Mirrors ollie's bias toward keeping *structure* even when *content* is
    sacrificed. SKELETON drops payloads but preserves the navigation tree
    so the LLM can drill into a specific span via ``read('span', id)``.
    """
    full_json = compact_json(data)
    full_tokens = estimate_tokens(full_json)

    budget = max_tokens if max_tokens is not None else TOKEN_FULL_THRESHOLD
    if full_tokens <= budget:
        return full_json, CompressionTier.FULL

    if full_tokens < TOKEN_SKELETON_THRESHOLD:
        truncated = truncate_strings(data, ".trace")
        return compact_json(truncated), CompressionTier.MEDIUM

    trace = data.get("trace") or {}
    spans = data.get("spans") or []
    skeleton = {
        "trace": {"id": trace.get("id"), "name": trace.get("name")},
        "spans": [
            {"id": s.get("id"), "name": s.get("name"), "type": s.get("type")}
            for s in spans
            if isinstance(s, dict)
        ],
        "spansTruncated": data.get("spansTruncated", False),
        "note": "SKELETON compression: payloads omitted. Use read('span', id) for details.",
    }
    return compact_json(skeleton), CompressionTier.SKELETON


def _compress_thread(data: dict[str, Any], max_tokens: int | None) -> tuple[str, CompressionTier]:
    """Thread+messages: FULL → MEDIUM (truncated strings) → SKELETON (turn list only).

    Mirrors ``_compress_trace``: SKELETON drops the message payloads but keeps
    the turn list so the LLM can drill into a specific turn via
    ``read('trace', trace_id)``.
    """
    full_json = compact_json(data)
    full_tokens = estimate_tokens(full_json)

    budget = max_tokens if max_tokens is not None else TOKEN_FULL_THRESHOLD
    if full_tokens <= budget:
        return full_json, CompressionTier.FULL

    if full_tokens < TOKEN_SKELETON_THRESHOLD:
        truncated = truncate_strings(data, ".thread")
        return compact_json(truncated), CompressionTier.MEDIUM

    thread = data.get("thread") or {}
    messages = data.get("messages") or []
    skeleton = {
        "thread": {"id": thread.get("id"), "status": thread.get("status")},
        "messages": [
            {
                "trace_id": m.get("trace_id"),
                "name": m.get("name"),
                "start_time": m.get("start_time"),
                "feedback_scores": m.get("feedback_scores"),
            }
            for m in messages
            if isinstance(m, dict)
        ],
        "messagesTruncated": data.get("messagesTruncated", False),
        "note": (
            "SKELETON compression: message payloads omitted. "
            "Use read('trace', trace_id) for details."
        ),
    }
    return compact_json(skeleton), CompressionTier.SKELETON


def _compress_issue(data: dict[str, Any], max_tokens: int | None) -> tuple[str, CompressionTier]:
    """Issue+details: FULL → MEDIUM (rows without metadata, then truncated
    strings) → SKELETON (ids only).

    Unlike trace/thread, MEDIUM here is budget-aware. An issue read is mostly
    per-day rows whose ``metadata`` repeats the example ids (already lifted
    into ``example_trace_ids``) and carries a confidence justification; that
    is the bulk of the tokens and none of the reason for the read. So MEDIUM
    first drops row metadata, then truncates long strings, and returns as
    soon as the body fits. If it still does not fit, SKELETON — the agent
    asked for a small answer, so the global 50k threshold is not the bar.
    """
    full_json = compact_json(data)
    full_tokens = estimate_tokens(full_json)

    budget = max_tokens if max_tokens is not None else TOKEN_FULL_THRESHOLD
    if full_tokens <= budget:
        return full_json, CompressionTier.FULL

    pruned = {
        **data,
        "details": [
            {key: value for key, value in row.items() if key != "metadata"}
            for row in data.get("details") or []
            if isinstance(row, dict)
        ],
    }
    pruned_json = compact_json(pruned)
    if estimate_tokens(pruned_json) <= budget:
        return pruned_json, CompressionTier.MEDIUM

    truncated_json = compact_json(truncate_strings(pruned, ".agent_insights_issue"))
    if estimate_tokens(truncated_json) <= budget:
        return truncated_json, CompressionTier.MEDIUM

    issue = data.get("issue") or {}
    skeleton: dict[str, Any] = {
        "issue": {
            "id": issue.get("id"),
            "name": issue.get("name"),
            "severity": issue.get("severity"),
            "status": issue.get("status"),
        },
        "example_trace_ids": data.get("example_trace_ids") or [],
    }
    for link_key in ("url", "trace_url_template"):
        if link_key in data:
            skeleton[link_key] = data[link_key]
    skeleton["note"] = (
        "SKELETON compression: cause, suggested fix and per-day details "
        "omitted. Use read('trace', trace_id) on an example, or re-read with "
        "since/until to narrow the window."
    )
    return compact_json(skeleton), CompressionTier.SKELETON


# --- registry ------------------------------------------------------------- #


ENTITY_REGISTRY: dict[str, EntityHandler] = {
    "project": EntityHandler(
        entity_type="project",
        fetch_fn=fetch_project,
        search_by_name_fn=_search_project,
        list_fn=_list_projects,
        # last_updated_trace_at lets the agent pick the project with live
        # traffic in one call instead of probing each one.
        list_extra_fields=("created_at", "last_updated_trace_at"),
        # The metrics endpoint takes instants, unlike the day-keyed Diagnostics one.
        read_window=ReadWindow("since", "until"),
        link_fn=project_links,
        description=(
            "A project's overview: the record, the last "
            f"{WINDOW_DAYS} days of SDK traffic against the {WINDOW_DAYS} before, "
            "the names it records, and what is freshest in it."
        ),
    ),
    "trace": EntityHandler(
        entity_type="trace",
        fetch_fn=_fetch_trace,
        list_fn=_list_traces,
        # Triage columns: what a "which traces need attention" list needs
        # without a read() per row. error_type is derived from error_info.
        list_extra_fields=("start_time", "duration", "error_type", "total_estimated_cost"),
        list_required_kwargs=("project_id",),
        compress_fn=_compress_trace,
        id_only=True,
        description=(
            "Single trace + child spans tree (up to 200 spans inlined). "
            "Returns {trace, spans, spansTruncated}."
        ),
    ),
    "span": EntityHandler(
        entity_type="span",
        fetch_fn=_fetch_span,
        list_fn=_list_spans,
        list_extra_fields=("type", "trace_id", "duration", "model", "error_type"),
        list_required_kwargs=("project_id",),
        id_only=True,
        description=(
            "Single span: inputs, outputs, metadata, timing, feedback_scores. "
            "list('span', project_id=…, filters=…) searches spans across a project."
        ),
    ),
    "test_suite": EntityHandler(
        entity_type="test_suite",
        fetch_fn=_fetch_test_suite,
        search_by_name_fn=_search_test_suite,
        list_fn=_list_test_suites,
        list_extra_fields=("created_at",),
        description=(
            "Opik 2.0 test suite (evaluation dataset). REST path is /datasets/{id} — "
            "test_suite is the conceptual name for the same backing entity."
        ),
    ),
    "experiment": EntityHandler(
        entity_type="experiment",
        fetch_fn=_fetch_experiment,
        search_by_name_fn=_search_experiment,
        list_fn=_list_experiments,
        # feedback_scores is the experiment's per-metric averages, rendered as
        # ``name=value`` pairs so a comparison list reads without a read() per row.
        list_extra_fields=("dataset_name", "created_at", "feedback_scores"),
        description="Experiment status + summary scores.",
    ),
    "prompt": EntityHandler(
        entity_type="prompt",
        fetch_fn=_fetch_prompt,
        search_by_name_fn=_search_prompt,
        list_fn=_list_prompts,
        list_extra_fields=("version_count", "created_at"),
        description=(
            "Prompt metadata + full version list. Returns {prompt, versions, versionsTruncated}."
        ),
    ),
    "test_suite_item": EntityHandler(
        entity_type="test_suite_item",
        fetch_fn=_unsupported_fetch,
        list_fn=_list_test_suite_items,
        list_extra_fields=("input", "expected_output"),
        list_required_kwargs=("test_suite_id",),
        id_only=True,
        description=(
            "Test suite item. Currently list-only — pass test_suite_id to enumerate. "
            "For full details, the parent test_suite read returns up to 200 items inline."
        ),
    ),
    "prompt_version": EntityHandler(
        entity_type="prompt_version",
        fetch_fn=_unsupported_fetch,
        list_fn=_list_prompt_versions,
        list_extra_fields=("template", "created_at"),
        list_required_kwargs=("prompt_id",),
        id_only=True,
        description=(
            "Prompt version. Currently list-only — pass prompt_id to enumerate. "
            "Use read('prompt', id) to get the prompt + all versions in one call."
        ),
    ),
    "thread": EntityHandler(
        entity_type="thread",
        fetch_fn=_fetch_thread,
        list_fn=_list_threads,
        # first_message stands in for the name a thread doesn't have: the
        # agent can pick the conversation without a read() per row.
        list_extra_fields=(
            "first_message",
            "status",
            "number_of_messages",
            "duration",
            "last_updated_at",
        ),
        list_required_kwargs=("project_id",),
        list_has_name=False,
        compress_fn=_compress_thread,
        id_only=True,
        needs_project=True,
        description=(
            "Conversation thread: metadata + messages list (each turn's trace "
            "input/output, up to 200 inlined). Returns {thread, messages, "
            "messagesTruncated}. Requires project scope — pass a thread link/URI "
            "or project_id. list('thread', project_id=…) enumerates a project's "
            "threads."
        ),
    ),
    "project_metric": EntityHandler(
        entity_type="project_metric",
        fetch_fn=_unsupported_fetch,
        # Present so the type is listable and reachable, but the list tool
        # delegates this entity whole to ``project_metrics.run_project_metric``
        # rather than driving it through the collection path: rows are time
        # buckets, the filter fields belong to whichever entity the metric is
        # about, and page/size/sort are meaningless. Six special cases in the
        # shared path, or one delegation — this is the delegation.
        list_fn=_delegated_elsewhere,
        # Deliberately declares no kwargs. The list tool returns before its
        # forwarding gate for this entity, so anything declared here would be
        # dead — and it was also already wrong (no `breakdown`, and
        # `project_name` is accepted). The runner validates its own arguments;
        # a second, unread copy of that contract is worse than none.
        description=(
            "One project metric over time, as a table of buckets. "
            "Reference: schema('list.project_metric')."
        ),
    ),
    "score_name": EntityHandler(
        entity_type="score_name",
        fetch_fn=_unsupported_fetch,
        list_fn=_list_score_names,
        list_required_kwargs=("project_id",),
        # Paged by us, not by the backend — the endpoint has no LIMIT, so the
        # slice happens after the fetch. The rows are strings; a page of them
        # is cheap either way.
        # No id: the backend's combined query returns distinct names only, and
        # no type: the service builds each entry from the name alone.
        list_has_id=False,
        list_footer=(
            "Names cover trace, span and thread scores together — the endpoint does "
            "not separate them, so a name alone does not say which kind it was "
            "attached to. Filtering the wrong kind returns an empty result, not an "
            "error."
        ),
        description=(
            "A feedback score name recorded in a project — what a score filter or a "
            "grouped score chart is named with. Trace, span and thread names come "
            "back together; the endpoint has no paging of its own, so pages are cut "
            "here."
        ),
    ),
    "online_rule": EntityHandler(
        entity_type="online_rule",
        fetch_fn=_unsupported_fetch,
        list_fn=_list_online_rules,
        list_extra_fields=("type", "enabled", "sampling_rate"),
        list_required_kwargs=("project_id",),
        description=(
            "An automation rule evaluator on a project — what scores the traces as "
            "they arrive, and so where most of its score names come from."
        ),
    ),
    "agent_insights_issue": EntityHandler(
        entity_type="agent_insights_issue",
        fetch_fn=_fetch_agent_insights_issue,
        list_fn=_list_agent_insights_issues,
        list_extra_fields=(
            "severity",
            "status",
            "total_occurrences",
            "latest_count",
            "last_seen",
        ),
        list_required_kwargs=("project_id",),
        list_optional_kwargs=("status", "from_date", "to_date"),
        page_note_fn=issue_page_note,
        # The Diagnostics endpoints key on whole UTC report days, so the
        # window is cut to dates rather than forwarded as instants. This
        # supersedes the `read_optional_kwargs=("from_date", "to_date")` that
        # declared the same window before the shape became per-entity.
        read_window=ReadWindow("from_date", "to_date", day_truncated=True),
        link_fn=_issue_links,
        compress_fn=_compress_issue,
        id_only=True,
        needs_project=True,
        description=(
            "Diagnostics issue (Agent Insights): a recurring failure the "
            "Diagnostics job grouped across a project's traces, with severity, "
            "status, occurrence counts, cause and suggested fix. "
            "list('agent_insights_issue', project_id=… | project_name=…) returns "
            "open issues ranked as the Diagnostics page ranks them (most recently "
            "seen first); pass status='resolved' or 'closed' for the rest. "
            "read('agent_insights_issue', id, project_id=…) returns {issue, "
            "example_trace_ids, details, url, trace_url_template}: the record with "
            "cause and suggested fix, the deduped ids of traces that exhibit it "
            "(open one with read('trace', id)), the per-day breakdown, and UI links "
            "to hand the user (omitted when the Opik URL or workspace is unknown). "
            "Counts are all-time unless since/until narrow the window (truncated "
            "to UTC report days)."
        ),
    ),
}


#: Short names accepted for an entity type, resolved before the registry
#: lookup. Deliberately not advertised in the tools' ``entity_type`` enum: the
#: enum is the closed set an agent should choose from, and listing a type twice
#: under two names invites the question of which is real. This is a safety net
#: for the guess an agent makes anyway — ``agent_insights_issue`` is a mouthful,
#: and "issue" is what the UI calls it.
ENTITY_ALIASES: dict[str, str] = {"issue": "agent_insights_issue"}


def resolve_entity_type(entity_type: str) -> str:
    """The registry name for ``entity_type``, mapping any alias."""
    return ENTITY_ALIASES.get(entity_type, entity_type)


READABLE_TYPES: tuple[str, ...] = tuple(
    t for t, h in ENTITY_REGISTRY.items() if h.fetch_fn is not _unsupported_fetch
)
LISTABLE_TYPES: tuple[str, ...] = tuple(
    t for t, h in ENTITY_REGISTRY.items() if h.list_fn is not None
)


def compress_for(
    handler: EntityHandler,
    data: dict[str, Any],
    max_tokens: int | None,
) -> tuple[str, CompressionTier]:
    if handler.compress_fn is not None:
        return handler.compress_fn(data, max_tokens)
    return generic_compress(data, entity_type=handler.entity_type, max_tokens=max_tokens)


__all__ = [
    "ENTITY_ALIASES",
    "ENTITY_REGISTRY",
    "LISTABLE_TYPES",
    "READABLE_TYPES",
    "SPANS_INLINE_LIMIT",
    "VERSIONS_INLINE_LIMIT",
    "EntityHandler",
    "compress_for",
]
