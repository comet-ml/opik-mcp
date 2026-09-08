"""Resolve a project name to its UUID for endpoints that take ``project_id`` only.

Traces and threads accept ``project_name`` natively, so the MCP passes it
through. The agent-insights (Diagnostics) endpoints do not, and the ``read`` /
``list`` contract already advertises ``project_name`` as an alternative to the
UUID for every project-scoped entity — so the MCP resolves it here rather than
teaching the agent an exception.

Resolution is deliberately strict. The projects endpoint is a substring search
(``name=demo`` also matches ``demo-2``), so only an exact, case-sensitive name
match counts. One match is used; several are listed back so the agent retries
with ``project_id``; none is a clear error. Silently picking the first
substring hit would read the wrong project's Diagnostics with nothing to say so.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient
from opik_mcp.read_list.errors import EntityArgValidationError

# Enough to see every exact match in any realistic workspace while keeping
# the lookup a single page. The substring search may return more than this
# many *partial* matches; exact ones are the only ones we keep.
_LOOKUP_PAGE_SIZE = 100


async def resolve_project_id(client: OpikListClient, project_name: str) -> str:
    """Exact-name project lookup. Raises ``EntityArgValidationError`` unless
    exactly one project carries ``project_name``."""
    page = await client.list_projects(name=project_name, page=1, size=_LOOKUP_PAGE_SIZE)
    exact: list[dict[str, Any]] = [
        item
        for item in page.get("content") or []
        if isinstance(item, dict)
        and item.get("name") == project_name
        and isinstance(item.get("id"), str)
        and item["id"]
    ]
    if len(exact) == 1:
        return str(exact[0]["id"])
    if not exact:
        raise EntityArgValidationError(
            f"No project named {project_name!r} in this workspace. Check the "
            f"spelling (the match is exact and case-sensitive), or find it with "
            f"list('project', name={project_name!r}) and pass its project_id."
        )
    lines = [
        f"Multiple projects are named {project_name!r}. Retry with project_id set "
        f"to one of these (or ask the user which they mean):",
    ]
    for item in exact[:10]:
        lines.append(f"  - project_id={item['id']}, name={item.get('name', '')!r}")
    raise EntityArgValidationError("\n".join(lines))


async def require_project_id(
    client: OpikListClient,
    *,
    project_id: str | None,
    project_name: str | None,
    caller: str,
) -> str:
    """Project scope for an endpoint that wants a UUID: the explicit id wins,
    otherwise the name is resolved, otherwise it is a validation error.

    ``caller`` names the tool call in the error (``"read('agent_insights_issue')"``)
    so the agent sees which argument list to fix.
    """
    if project_id is not None:
        return project_id
    if project_name is None:
        raise EntityArgValidationError(f"{caller} requires project_id or project_name.")
    return await resolve_project_id(client, project_name)


__all__ = ["require_project_id", "resolve_project_id"]
