"""``span`` — one span by id, and a project's spans as a page.

A span read is the flat record. Nothing is inlined: a span's children are
spans of the same project, and the caller asked for one.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_span(entity_id)


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Project-wide span search: no ``trace_id`` — that scoping (and ``type``)
    # is expressed in OQL (``trace_id = "…"``, ``type = "llm"``). ``name``
    # filtering isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_spans(**kw)


HANDLER = EntityHandler(
    entity_type="span",
    fetch_fn=fetch,
    list_fn=list_page,
    list_extra_fields=("type", "trace_id", "duration", "model", "error_type"),
    list_required_kwargs=("project_id",),
    id_only=True,
    description=(
        "Single span: inputs, outputs, metadata, timing, feedback_scores. "
        "list('span', project_id=…, filters=…) searches spans across a project."
    ),
)
