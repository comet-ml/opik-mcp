"""``project`` — the overview a session starts from.

The record on its own carries no numbers: ``GET /projects/{id}`` serves the
public projection and every aggregate lives on the detailed one. So a project
read fans out, and the parts are one file each:

- ``summary`` is the four figures the Logs page shows, for a window against
  the window before it.
- ``vocabulary`` is the names the project records, which is what the next
  question has to be written in.
- ``contents`` is what is freshest in it: an experiment, a dataset, a prompt
  version, an optimization run.
- ``read`` assembles them.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.entities.project.read import fetch_project, project_links
from opik_mcp.read_list.entities.project.summary import WINDOW_DAYS
from opik_mcp.read_list.handler import EntityHandler, PageContext, ReadWindow
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.ui_links import row_link_template


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_projects(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_projects(**kw)


async def _row_link_note(
    client: OpikListClient, settings: Settings, ctx: PageContext
) -> str | None:
    """One link for the whole page, with the row's own columns left as slots.

    Not one url per row: the rows share a project, so only the columns the
    table already prints vary, and the page pays for one link instead of a page of them.
    """
    if ctx.empty:
        return None
    note = row_link_template(settings, "project", ctx.project_id)
    if note is None:
        return None
    return (
        "Open a row in Opik: " + note["url_template"] + " — fill the slots from "
        "the row's own columns. Show it to the user as a link named after the "
        "row, never as a bare URL."
    )


HANDLER = EntityHandler(
    entity_type="project",
    page_note_fn=_row_link_note,
    fetch_fn=fetch_project,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
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
)

__all__ = ["HANDLER"]
