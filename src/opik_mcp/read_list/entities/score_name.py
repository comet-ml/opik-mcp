"""``score_name`` — the feedback score names a project has recorded.

What a score filter or a grouped score chart is named with. The endpoint is
the multi-project one narrowed to one project, and it answers with distinct
names and nothing else: no id, no entity kind, no paging. So the id column is
dropped, the page is cut here, and the listing says what a name cannot.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.project_scope import scope_of
from opik_mcp.read_list.unsupported import unsupported_fetch


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
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


HANDLER = EntityHandler(
    entity_type="score_name",
    fetch_fn=unsupported_fetch,
    list_fn=list_page,
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
)
