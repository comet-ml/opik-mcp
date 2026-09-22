"""``span`` — one span by id, and a project's spans as a page.

A span read is the flat record. Nothing is inlined: a span's children are
spans of the same project, and the caller asked for one.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler, PageContext
from opik_mcp.read_list.ui_links import row_link_template, trace_page_url


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_span(entity_id)


def span_links(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """The span, open inside its trace.

    A span has no page and no panel of its own: the Logs spans view lists
    them but opens nothing, and ``?span=`` is a selection *within* an already
    opened trace panel — the UI writes an empty one into the address when a
    trace is opened without a span. So the address is the trace's, with the
    span named; it opens the trace panel with this span selected in the tree.

    Both parts come from the record, and a span whose trace is unknown gets no
    link rather than one that lands on a list it is not on.
    """
    project_id = data.get("project_id")
    trace_id = data.get("trace_id")
    span_id = data.get("id")
    if not all(isinstance(v, str) and v for v in (project_id, trace_id, span_id)):
        return {}
    url = trace_page_url(settings, str(project_id), str(trace_id), span_id=str(span_id))
    return {"url": url} if url is not None else {}


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Project-wide span search: no ``trace_id`` — that scoping (and ``type``)
    # is expressed in OQL (``trace_id = "…"``, ``type = "llm"``). ``name``
    # filtering isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_spans(**kw)


async def _row_link_note(
    client: OpikListClient, settings: Settings, ctx: PageContext
) -> str | None:
    """One link for the whole page, with the row's own columns left as slots.

    Not one url per row: the rows share a project, so only the columns the
    table already prints vary, and the page pays for one link instead of a page of them.
    """
    if ctx.empty:
        return None
    note = row_link_template(settings, "span", ctx.project_id)
    if note is None:
        return None
    return (
        "Open a row in Opik: " + note["url_template"] + " — fill the slots from "
        "the row's own columns. Show it to the user as a link named after the "
        "row, never as a bare URL."
    )


HANDLER = EntityHandler(
    entity_type="span",
    page_note_fn=_row_link_note,
    fetch_fn=fetch,
    link_fn=span_links,
    list_fn=list_page,
    list_extra_fields=("type", "trace_id", "duration", "model", "error_type"),
    list_required_kwargs=("project_id",),
    id_only=True,
    description=(
        "Single span: inputs, outputs, metadata, timing, feedback_scores. "
        "list('span', project_id=…, filters=…) searches spans across a project."
    ),
)
