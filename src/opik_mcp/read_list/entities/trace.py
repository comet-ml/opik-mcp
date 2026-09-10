"""``trace`` — one request through an instrumented app, with its spans.

The read inlines the span tree, because a trace on its own says a call
happened and the spans say what it did. Its compression is the one place a
generic truncation is not enough: a trace that busts the budget is usually
one huge input or output, and the skeleton keeps the shape of every span
while dropping the payloads, so the agent can still see where to look.
"""

from __future__ import annotations

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
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import collection_truncated, page_items

# Inline caps for composite reads — match the previous resources.py
# constants so cache shapes stay stable for any in-flight integration.
SPANS_INLINE_LIMIT = 200


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
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
    spans = page_items(spans_page)
    truncated = collection_truncated(spans_page, inlined=len(spans), limit=SPANS_INLINE_LIMIT)
    return {"trace": trace, "spans": spans, "spansTruncated": truncated}


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Trace listing is project-scoped — ``list`` tool enforces project_id
    # presence via ``list_required_kwargs``. ``name`` filtering on traces
    # isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_traces(**kw)


def compress(data: dict[str, Any], max_tokens: int | None) -> tuple[str, CompressionTier]:
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


HANDLER = EntityHandler(
    entity_type="trace",
    fetch_fn=fetch,
    list_fn=list_page,
    # Triage columns: what a "which traces need attention" list needs
    # without a read() per row. error_type is derived from error_info.
    list_extra_fields=("start_time", "duration", "error_type", "total_estimated_cost"),
    list_required_kwargs=("project_id",),
    compress_fn=compress,
    id_only=True,
    description=(
        "Single trace + child spans tree (up to 200 spans inlined). "
        "Returns {trace, spans, spansTruncated}."
    ),
)
