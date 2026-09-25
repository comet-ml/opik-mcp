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

from typing import Any, Final

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.entities.project.read import fetch_project, project_links
from opik_mcp.read_list.entities.project.summary import WINDOW_DAYS
from opik_mcp.read_list.handler import EntityHandler, ReadWindow, Vocabulary
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.ui_links import project_page_url

#: Stands in for the project while the row template is built, so the builder
#: is handed something that is an id in shape if not in meaning.
_SLOT: Final = "0PROJECTSLOT0"


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_projects(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_projects(**kw)


def row_link_template(settings: Settings, _page_project_id: str | None) -> str | None:
    """The row *is* the project, so the project slot is the id column.

    The placeholder is substituted after the url is built rather than passed
    in as the project: ``project_page_url`` takes an id, and handing it a
    template slot would make its emptiness check the only thing standing
    between a slot and a path segment.
    """
    built = project_page_url(settings, _SLOT, "logs")
    return None if built is None else built.replace(_SLOT, "{id}")


VOCABULARY = Vocabulary(
    name="project",
    sort_fields=("id", "name", "created_at", "last_updated_at", "last_updated_trace_at"),
)
"""Transcribed from ``SortingFactoryProjects``.

``last_updated_trace_at`` is the one that answers a real question: a
workspace accumulates throwaway projects, and the list arrives ordered by
creation, so "which project is actually live" meant reading fifteen rows and
comparing two date columns by eye.
"""


HANDLER = EntityHandler(
    entity_type="project",
    vocabularies=(VOCABULARY,),
    row_link_template=row_link_template,
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
