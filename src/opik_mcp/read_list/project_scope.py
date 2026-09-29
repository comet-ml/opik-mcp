"""Resolve a project name to its UUID for endpoints that take ``project_id`` only.

Traces and threads accept ``project_name`` natively, so the MCP passes it
through. The agent-insights (Diagnostics) endpoints do not, and the ``read`` /
``list`` contract already advertises ``project_name`` as an alternative to the
UUID for every project-scoped entity — so the MCP resolves it here rather than
teaching the agent an exception.

Resolution matches the whole name, the way the backend matches ``project_name``
on the trace/thread endpoints (case-insensitively), so the same argument
behaves the same on every project-scoped entity. The projects endpoint is a
substring search (``name=demo`` also matches ``demo-2``), so substring hits
are never used: one whole-name match is used (exact case first); several are
listed back so the agent retries with ``project_id``; none is a clear error.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import time
from contextvars import ContextVar
from typing import Any

import httpx

from opik_mcp.client.base import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.client.protocols import OpikListClient
from opik_mcp.cost_intelligence import COST_INTELLIGENCE_MODE, FIXED_PROJECT, Mode
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import short_list
from opik_mcp.read_list.uri import is_uuid

logger = logging.getLogger("opik_mcp.read_list.project_scope")

# Enough to see every exact match in any realistic workspace while keeping
# the lookup a single page. The substring search may return more than this
# many *partial* matches; exact ones are the only ones we keep.
_LOOKUP_PAGE_SIZE = 100

# The agent's normal flow is list-then-read with the same project_name, and
# each call used to pay the projects round trip again (~370 ms on cloud). A
# project's id never changes, and a rename is rare, so a short-lived cache is
# safe. The key is the client's REST base, workspace AND a hash of its
# credential, plus the name: in hosted mode one process serves many tenants,
# the same name means a different project in each, and under OAuth
# passthrough the workspace is token-derived server-side and may be unknown
# here — the credential is then the only thing that tells tenants apart.
_CACHE_TTL_SECONDS = 300.0
_CACHE_MAX_ENTRIES = 512
_CacheKey = tuple[str | None, str | None, str | None, str]
_cache: dict[_CacheKey, tuple[str, float]] = {}

# Cost intelligence mode serves one project, so a project name that resolves
# to nothing must not list the workspace's others.
_ONLY_FIXED_PROJECT = f"This cost intelligence workspace only serves the `{FIXED_PROJECT}` project."
_NO_FIXED_PROJECT = (
    f"This cost intelligence workspace has no `{FIXED_PROJECT}` project yet. "
    "Claude Code usage appears there once data arrives."
)
_CONFINED: ContextVar[bool] = ContextVar("confined_to_fixed_project", default=False)


def _str_attr(client: OpikListClient, name: str) -> str | None:
    value = getattr(client, name, None)
    return value if isinstance(value, str) and value else None


def _cache_key(client: OpikListClient, project_name: str) -> _CacheKey:
    credential = _str_attr(client, "_api_key")
    credential_hash = (
        hashlib.sha256(credential.encode()).hexdigest()[:16] if credential is not None else None
    )
    return (
        _str_attr(client, "_base_url"),
        _str_attr(client, "_workspace"),
        credential_hash,
        project_name,
    )


def _cache_get(key: _CacheKey) -> str | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    project_id, expires_at = hit
    if time.monotonic() >= expires_at:
        _cache.pop(key, None)
        return None
    return project_id


def _cache_put(key: _CacheKey, project_id: str) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        # Bounded and rarely full; dropping the oldest insertion is enough.
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = (project_id, time.monotonic() + _CACHE_TTL_SECONDS)


async def project_rows(client: OpikListClient, *, name: str | None = None) -> list[dict[str, Any]]:
    """One page of projects for the side lookups (did-you-mean, last-trace hint).

    ``name`` is the backend's substring filter, so the caller still matches
    the whole name. Failures are logged and yield ``[]``: these lookups
    decorate an answer the agent already has and must never replace it with
    an error.
    """
    try:
        body = (
            await client.list_projects(name=name, size=_LOOKUP_PAGE_SIZE)
            if name
            else await client.list_projects(size=_LOOKUP_PAGE_SIZE)
        )
    except (
        OpikAuthError,
        OpikNotFoundError,
        OpikValidationError,
        OpikServerError,
        httpx.HTTPError,
    ):
        logger.debug("project lookup for a hint failed; skipping the hint", exc_info=True)
        return []
    return [p for p in body.get("content") or [] if isinstance(p, dict)]


async def unknown_project_message(client: OpikListClient, project_name: str) -> str:
    """One extra call when a project name resolves to nothing: the names that
    do exist, and the closest one. A typo is the common case, and every
    project-scoped entity answers it with the same words."""
    if _CONFINED.get():
        return _NO_FIXED_PROJECT
    names = [p["name"] for p in await project_rows(client) if isinstance(p.get("name"), str)]
    message = f"Project {project_name!r} not found."
    close = difflib.get_close_matches(project_name, names, n=1, cutoff=0.6)
    if close:
        message += f" Did you mean {close[0]!r}?"
    if names:
        message += f" Projects: {', '.join(names)}."
    return message


def reset_project_cache_for_tests() -> None:
    """Drop every cached project id. Test-only: fakes carry no credential, so
    they all share one key and one test's resolution would satisfy the next."""
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
    """Whole-name match, the way the backend matches ``project_name`` on the
    trace/thread endpoints: case-insensitive, never a substring. An exact-case
    match wins over a case-insensitive one so ``demo`` and ``Demo`` can coexist;
    several case-insensitive matches are listed back rather than guessed."""
    page = await client.list_projects(name=project_name, page=1, size=_LOOKUP_PAGE_SIZE)
    named: list[dict[str, Any]] = [
        item
        for item in page.get("content") or []
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("id"), str)
        and item["id"]
    ]
    exact = [item for item in named if item["name"] == project_name]
    if len(exact) == 1:
        return str(exact[0]["id"])
    folded = project_name.casefold()
    matches = exact or [item for item in named if item["name"].casefold() == folded]
    if len(matches) == 1:
        return str(matches[0]["id"])
    if not matches:
        raise EntityArgValidationError(await unknown_project_message(client, project_name))
    lines = [
        f"Multiple projects match the name {project_name!r}. Retry with project_id "
        f"set to one of these (or ask the user which they mean):",
    ]
    lines.extend(
        short_list([f"  - project_id={item['id']}, name={item['name']!r}" for item in matches])
    )
    raise EntityArgValidationError("\n".join(lines))


#: The project a listing resolved for itself, for the length of one call.
#:
#: A name-scoped list does not round-trip the name into an id — that is what
#: makes ``project_name`` as cheap as ``project_id`` — except where the
#: endpoint takes a UUID and the listing had to resolve one anyway. Where it
#: did, the answer is worth keeping: a page decoration needs the same id, and
#: asking again would spend a second call to learn what this one already
#: knows. A ContextVar and not a global because two calls can be in flight.
_RESOLVED_PROJECT: ContextVar[str | None] = ContextVar("resolved_list_project", default=None)


def remember_resolved_project(project_id: str | None) -> None:
    """Record (or, with ``None``, clear) the project this call resolved."""
    _RESOLVED_PROJECT.set(project_id)


def resolved_project() -> str | None:
    """The project this call resolved for itself, if it had to resolve one."""
    return _RESOLVED_PROJECT.get()


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
    resolved = await resolve_project_id(client, project_name)
    remember_resolved_project(resolved)
    return resolved


async def scope_of(client: OpikListClient, kw: dict[str, Any], *, caller: str) -> str:
    """The project a ``list_fn`` is scoped to, from whichever spelling arrived.

    Every project-scoped list takes ``project_id`` or ``project_name`` and
    forwards them in its ``**kw``; three of them had written the same
    six-line unpacking of that pair. The read path keeps
    :func:`require_project_id` directly — it has the two as real parameters.
    """
    return await require_project_id(
        client,
        project_id=kw.get("project_id"),
        project_name=kw.get("project_name"),
        caller=caller,
    )


def enter_mode(mode: Mode) -> None:
    """Record, for this call, whether it is confined to the fixed project."""
    _CONFINED.set(mode == COST_INTELLIGENCE_MODE)


def is_confined() -> bool:
    """Whether this call is confined to the fixed project (cost intelligence mode)."""
    return _CONFINED.get()


def _is_scoped(handler: EntityHandler, mode: Mode, scope: str) -> bool:
    return mode == COST_INTELLIGENCE_MODE and handler.project_scope == scope


def subject_record(entity_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """The record an answer is about: a composite keeps it under the entity's
    name, anything else is the answer itself."""
    subject = data.get(entity_type)
    return subject if isinstance(subject, dict) else data


def scoped_project_args(
    handler: EntityHandler, mode: Mode, *, project_id: str | None, project_name: str | None
) -> tuple[str | None, str | None]:
    """The project scope a project-owned entity is called with in cost intelligence
    mode: the fixed project when none was given, a refusal for another name."""
    if not _is_scoped(handler, mode, "parent"):
        return project_id, project_name
    if project_name is not None and project_name.casefold() != FIXED_PROJECT.casefold():
        raise EntityArgValidationError(_ONLY_FIXED_PROJECT)
    # The fixed spelling, so a differently-cased twin project is never matched.
    if project_id is None:
        return None, FIXED_PROJECT
    return project_id, None if project_name is None else FIXED_PROJECT


def scoped_list_name(handler: EntityHandler, mode: Mode, name: str | None) -> str | None:
    """The name filter a project listing runs with: always the fixed project.

    A caller's name is only checked, so a substring never widens the backend page."""
    if not _is_scoped(handler, mode, "self"):
        return name
    if name is not None and name.casefold() not in FIXED_PROJECT.casefold():
        raise EntityArgValidationError(_ONLY_FIXED_PROJECT)
    return FIXED_PROJECT


def refuse_other_project_name(handler: EntityHandler, mode: Mode, record_id: str) -> None:
    """Refuse a plain project name that is not the fixed project, before any fetch."""
    is_other_name = record_id.casefold() != FIXED_PROJECT.casefold() and not is_uuid(record_id)
    if _is_scoped(handler, mode, "self") and is_other_name:
        raise EntityArgValidationError(_ONLY_FIXED_PROJECT)


async def verify_project_id(
    client: OpikListClient, handler: EntityHandler, mode: Mode, project_id: str | None
) -> None:
    """Refuse a project id that is not the fixed project's."""
    if project_id is None or not _is_scoped(handler, mode, "parent"):
        return
    fixed_id = await resolve_project_id(client, FIXED_PROJECT)
    if project_id.casefold() != fixed_id.casefold():
        raise EntityArgValidationError(_ONLY_FIXED_PROJECT)


async def verify_record_id(
    client: OpikListClient, handler: EntityHandler, mode: Mode, record_id: str
) -> None:
    """Refuse another project's UUID before any fetch runs."""
    if not _is_scoped(handler, mode, "self") or not is_uuid(record_id):
        return
    fixed_id = await resolve_project_id(client, FIXED_PROJECT)
    if record_id.casefold() != fixed_id.casefold():
        raise EntityArgValidationError(_ONLY_FIXED_PROJECT)


async def verify_record(
    client: OpikListClient, handler: EntityHandler, mode: Mode, data: dict[str, Any]
) -> None:
    """Refuse a fetched record that belongs to another project.

    A record with no project id is trusted only where the fetch was itself
    scoped to the project (``needs_project``).
    """
    if not (_is_scoped(handler, mode, "parent") or _is_scoped(handler, mode, "self")):
        return
    record = subject_record(handler.entity_type, data)
    owner = record.get("id" if handler.project_scope == "self" else "project_id")
    if owner is None and handler.needs_project:
        return
    fixed_id = await resolve_project_id(client, FIXED_PROJECT)
    if not isinstance(owner, str) or owner.casefold() != fixed_id.casefold():
        raise EntityArgValidationError(_ONLY_FIXED_PROJECT)


async def keep_fixed_project_row(
    client: OpikListClient,
    handler: EntityHandler,
    mode: Mode,
    page: dict[str, Any],
    *,
    page_number: int = 1,
) -> dict[str, Any]:
    """A project listing narrowed to the fixed project, with its total recomputed.

    The row kept is the one whose id is the resolved fixed project's, the test
    every read uses. Only a missing project is refused; a page past the single
    row stays empty and still counts the one project."""
    if not _is_scoped(handler, mode, "self"):
        return page
    candidates = [row for row in page.get("content") or [] if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    if candidates:
        fixed_id = await resolve_project_id(client, FIXED_PROJECT)
        rows = [row for row in candidates if str(row.get("id")).casefold() == fixed_id.casefold()]
    if not rows:
        if page_number > 1 and page.get("total"):
            return {**page, "content": [], "total": 1}
        raise EntityArgValidationError(_NO_FIXED_PROJECT)
    return {**page, "content": rows, "total": len(rows)}


__all__ = [
    "enter_mode",
    "is_confined",
    "keep_fixed_project_row",
    "project_rows",
    "refuse_other_project_name",
    "remember_resolved_project",
    "require_project_id",
    "reset_project_cache_for_tests",
    "resolve_project_id",
    "resolved_project",
    "scope_of",
    "scoped_list_name",
    "scoped_project_args",
    "subject_record",
    "unknown_project_message",
    "verify_project_id",
    "verify_record",
    "verify_record_id",
]
