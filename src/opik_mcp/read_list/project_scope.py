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

import time
from typing import Any

from opik_mcp.opik_client import OpikListClient
from opik_mcp.read_list.errors import EntityArgValidationError

# Enough to see every exact match in any realistic workspace while keeping
# the lookup a single page. The substring search may return more than this
# many *partial* matches; exact ones are the only ones we keep.
_LOOKUP_PAGE_SIZE = 100

# The agent's normal flow is list-then-read with the same project_name, and
# each call used to pay the projects round trip again (~370 ms on cloud). A
# project's id never changes, and a rename is rare, so a short-lived cache is
# safe. Keyed by the client's REST base + workspace as well as the name: in
# hosted mode one process serves many workspaces, and the same name means a
# different project in each.
_CACHE_TTL_SECONDS = 300.0
_CACHE_MAX_ENTRIES = 512
_cache: dict[tuple[str | None, str | None, str], tuple[str, float]] = {}


def _cache_key(client: OpikListClient, project_name: str) -> tuple[str | None, str | None, str]:
    base_url = getattr(client, "_base_url", None)
    workspace = getattr(client, "_workspace", None)
    return (
        base_url if isinstance(base_url, str) else None,
        workspace if isinstance(workspace, str) else None,
        project_name,
    )


def _cache_get(key: tuple[str | None, str | None, str]) -> str | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    project_id, expires_at = hit
    if time.monotonic() >= expires_at:
        _cache.pop(key, None)
        return None
    return project_id


def _cache_put(key: tuple[str | None, str | None, str], project_id: str) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        # Bounded and rarely full; dropping the oldest insertion is enough.
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = (project_id, time.monotonic() + _CACHE_TTL_SECONDS)


def reset_project_cache_for_tests() -> None:
    _cache.clear()


async def resolve_project_id(client: OpikListClient, project_name: str) -> str:
    """Exact-name project lookup. Raises ``EntityArgValidationError`` unless
    exactly one project carries ``project_name``.

    Successful resolutions are cached per (REST base, workspace, name) for a
    few minutes; misses and ambiguities are not, since the project may be
    created or renamed a moment later.
    """
    key = _cache_key(client, project_name)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    project_id = await _lookup_project_id(client, project_name)
    _cache_put(key, project_id)
    return project_id


async def _lookup_project_id(client: OpikListClient, project_name: str) -> str:
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


__all__ = ["require_project_id", "reset_project_cache_for_tests", "resolve_project_id"]
