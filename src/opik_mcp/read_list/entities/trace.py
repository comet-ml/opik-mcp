"""``trace`` — one request through an instrumented app, with its spans.

The read inlines the span tree, because a trace on its own says a call
happened and the spans say what it did.

The trace itself arrives whole — the payload that makes a trace large is
usually the payload the read was opened for. The spans are asked for slim:
two hundred of them is where a single base64 image turns one read into the
whole context window, and each carries the id that fetches it back in full.
See ``read_list/slim.py``.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import (
    collection_total,
    collection_truncated,
    continuation,
    page_items,
    rest_of,
)
from opik_mcp.read_list.slim import count_cut, slim_notice

# Inline caps for composite reads — match the previous resources.py
# constants so cache shapes stay stable for any in-flight integration.
SPANS_INLINE_LIMIT = 200

#: The span fields ``truncate=true`` acts on, in the order the backend cuts
#: them. Used to count what actually lost bytes, not to cut anything here.
SLIM_SPAN_FIELDS = ("input", "output", "metadata")


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    """Trace + inlined spans (up to ``SPANS_INLINE_LIMIT``).

    The spans index in opik-backend is sharded by project, so the second
    call needs the trace's ``project_id``. A trace without project_id is
    anomalous — return an empty spans list rather than failing the read.

    ``spanBodies`` says how many spans lost bytes to the backend's cut. It is
    absent when no spans arrived — from an empty tree or a failed call — since
    a notice about a payload the caller never received is only noise they have
    to reason about.
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
            truncate=True,
        )
    except Exception:
        return {"trace": trace, "spans": [], "spansTruncated": False}
    spans = page_items(spans_page)
    truncated = collection_truncated(spans_page, inlined=len(spans), limit=SPANS_INLINE_LIMIT)
    result: dict[str, Any] = {"trace": trace, "spans": spans, "spansTruncated": truncated}
    if truncated:
        # An agent loop can run to hundreds of spans; the flag alone left the
        # caller knowing the tree was short and not how to see the rest.
        result["moreSpans"] = rest_of(
            "spans",
            inlined=len(spans),
            total=collection_total(spans_page),
            call=(
                f"list('span', project_id='{project_id}', "
                f"filters='trace_id = \"{entity_id}\"', {continuation(SPANS_INLINE_LIMIT)})"
            ),
        )
    if spans:
        result["spanBodies"] = slim_notice(
            cut=count_cut(spans, SLIM_SPAN_FIELDS),
            total=len(spans),
            noun="span",
            whole="read('span', id)",
        )
    return result


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Trace listing is project-scoped — ``list`` tool enforces project_id
    # presence via ``list_required_kwargs``. ``name`` filtering on traces
    # isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_traces(**kw)


HANDLER = EntityHandler(
    entity_type="trace",
    fetch_fn=fetch,
    list_fn=list_page,
    # Triage columns: what a "which traces need attention" list needs
    # without a read() per row. error_type is derived from error_info.
    list_extra_fields=("start_time", "duration", "error_type", "total_estimated_cost"),
    list_required_kwargs=("project_id",),
    id_only=True,
    description=(
        "Single trace + child spans tree (up to 200 spans inlined, bodies "
        "slim). Returns {trace, spans, spansTruncated}, plus spanBodies "
        "saying what the cut took when any span was inlined, and moreSpans "
        "with the call for the rest when the tree is longer than 200."
    ),
)
