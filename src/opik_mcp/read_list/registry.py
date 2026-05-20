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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

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

# Inline caps for composite reads — match the previous resources.py
# constants so cache shapes stay stable for any in-flight integration.
SPANS_INLINE_LIMIT = 200
VERSIONS_INLINE_LIMIT = 100

FetchFn = Callable[[OpikReadClient, str], Awaitable[dict[str, Any]]]
SearchByNameFn = Callable[[OpikReadClient, str], Awaitable[list[dict[str, Any]]]]
ListFn = Callable[..., Awaitable[dict[str, Any]]]
CompressFn = Callable[[dict[str, Any], int | None], tuple[str, CompressionTier]]


@dataclass(frozen=True)
class EntityHandler:
    entity_type: str
    fetch_fn: FetchFn
    description: str
    search_by_name_fn: SearchByNameFn | None = None
    list_fn: ListFn | None = None
    list_extra_fields: tuple[str, ...] = ()
    list_required_kwargs: tuple[str, ...] = ()
    compress_fn: CompressFn | None = None
    id_only: bool = False
    """True if the entity is addressed only by UUID (no name lookup).

    The ``read`` tool uses this to skip the name-lookup branch entirely —
    saves one round-trip on every non-UUID input for traces/spans/etc.
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


async def _fetch_project(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_project(entity_id)


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


async def _unsupported_fetch(_client: OpikReadClient, _entity_id: str) -> dict[str, Any]:
    """Sentinel for list-only entities. The read tool raises before calling this."""
    raise NotImplementedError(
        "This entity is list-only — use list() with the parent id, or read the parent entity."
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


# --- registry ------------------------------------------------------------- #


ENTITY_REGISTRY: dict[str, EntityHandler] = {
    "project": EntityHandler(
        entity_type="project",
        fetch_fn=_fetch_project,
        search_by_name_fn=_search_project,
        list_fn=_list_projects,
        list_extra_fields=("created_at",),
        description="Project metadata + stats (trace_count, last activity).",
    ),
    "trace": EntityHandler(
        entity_type="trace",
        fetch_fn=_fetch_trace,
        list_fn=_list_traces,
        list_extra_fields=("start_time", "end_time"),
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
        id_only=True,
        description="Single span: inputs, outputs, metadata, timing, feedback_scores.",
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
        list_extra_fields=("dataset_name", "created_at"),
        description="Experiment status + summary scores. Pair with ask_ollie for analysis.",
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
}


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
    "ENTITY_REGISTRY",
    "LISTABLE_TYPES",
    "READABLE_TYPES",
    "SPANS_INLINE_LIMIT",
    "VERSIONS_INLINE_LIMIT",
    "EntityHandler",
    "compress_for",
]
