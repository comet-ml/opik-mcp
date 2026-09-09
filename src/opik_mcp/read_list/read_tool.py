"""``read`` tool — fetches any Opik entity by id (or name) with compression.

Ported from ollie-assist's ``tools/read/tool.py``, adapted to opik-mcp's
``OpikClient`` instead of the Opik SDK. The agent-facing contract is:

    read(entity_type, id, max_tokens=None) -> str

The returned string is a one-line ``[read: …]`` header followed by JSON
(compressed per the entity's compression tier). Errors come back as
``ToolError`` with status-specific guidance — same shape as ollie so the
LLM's error-recovery prompting is portable.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikPermissionError,
    OpikReadClient,
    OpikServerError,
    OpikValidationError,
    client_for_call,
)
from opik_mcp.read_list.compression import compact_json, estimate_tokens, size_header
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.registry import (
    ENTITY_REGISTRY,
    READABLE_TYPES,
    EntityHandler,
    compress_for,
)
from opik_mcp.read_list.uri import InvalidURI, looks_like_opik_link, looks_like_uri
from opik_mcp.read_list.uri import parse as parse_uri
from opik_mcp.read_list.window import WindowError, format_instant, resolve_window

logger = logging.getLogger("opik_mcp.read_list.read")

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s))


def _format_ambiguous(entity_type: str, name: str, candidates: list[dict[str, Any]]) -> str:
    lines = [
        f"Multiple {entity_type}s match name {name!r}. "
        "Use read() with one of these UUIDs (or ask the user which they mean):",
    ]
    for c in candidates[:10]:
        lines.append(f"  - id={c.get('id')}, name={c.get('name', '')!r}")
    return "\n".join(lines)


def _format_client_error(
    entity_type: str,
    entity_id: str,
    exc: BaseException,
) -> str:
    """Map our typed OpikClient errors to agent-friendly messages.

    Mirrors ollie's ``_format_status_error`` shape (status-aware hints for
    404 / 403 / 422 / 5xx) but reads off the typed-exception hierarchy
    instead of raw HTTP codes.
    """
    if isinstance(exc, OpikNotFoundError):
        return (
            f"Not found: {entity_type} with id '{entity_id}'. "
            "Verify the ID is a valid UUID and belongs to the current workspace. "
            f"Detail: {exc}"
        )
    if isinstance(exc, OpikPermissionError):
        return (
            f"Permission denied fetching {entity_type} '{entity_id}'. "
            "The current workspace may not have access to this entity. "
            f"Detail: {exc}"
        )
    if isinstance(exc, OpikAuthError):
        # A 401 is about the credential, not the workspace: an expired OAuth
        # token or a bad API key. The client error already says which and what
        # to do about it; wrapping it in "permission denied" sent users (and the
        # model) hunting for workspace access that was never the problem.
        return f"Authentication failed fetching {entity_type} '{entity_id}'. Detail: {exc}"
    if isinstance(exc, OpikValidationError):
        return (
            f"Validation error fetching {entity_type} '{entity_id}': "
            "the request was missing or had invalid parameters. "
            f"Detail: {exc}"
        )
    if isinstance(exc, OpikServerError):
        return (
            f"Opik backend error fetching {entity_type} '{entity_id}'. "
            "This is a server-side issue and may be transient. "
            f"Detail: {exc}"
        )
    return f"Failed to fetch {entity_type} '{entity_id}': {exc}"


async def run_read(
    entity_type: str,
    id: str,
    *,
    max_tokens: int | None = None,
    project_id: str | None = None,
    project_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    settings: Settings | None = None,
    client: OpikReadClient | None = None,
    **entity_kwargs: Any,
) -> str:
    """Read tool entrypoint. See ``server.py`` for the registered tool.

    Dispatch order: URI parse → registry lookup → project gate → UUID-vs-name
    branch → fetch → compress. Each branch surfaces errors as ``ToolError`` so
    the host LLM gets the structured guidance.
    """
    # Accept ``opik://…`` URIs and pasted web links (thread panel, Diagnostics
    # page) as id input. When the URI encodes its own entity_type we trust it
    # and override the explicit argument — that way the agent can paste a URI
    # into either slot. A parsed project-scoped URI/link also carries the
    # project, which overrides the explicit arg.
    if looks_like_uri(id) or looks_like_opik_link(id):
        try:
            parsed = parse_uri(id)
        except InvalidURI as e:
            raise ToolError(str(e)) from e
        entity_type = parsed.entity_type
        id = parsed.entity_id
        if parsed.project_id is not None:
            # The URI is the source of truth for the project — clear any
            # explicit project_name so we don't send a conflicting pair.
            project_id = parsed.project_id
            project_name = None

    if entity_type not in READABLE_TYPES:
        if entity_type in ENTITY_REGISTRY:
            err = EntityArgValidationError(
                f"Entity {entity_type!r} is list-only — use list({entity_type!r}, "
                f"<parent_id>=…) to enumerate, or read the parent entity instead."
            )
            raise ToolError(str(err)) from err
        valid = ", ".join(sorted(READABLE_TYPES))
        err = EntityArgValidationError(
            f"Invalid entity_type {entity_type!r}. Readable types: {valid}"
        )
        raise ToolError(str(err)) from err

    handler = ENTITY_REGISTRY[entity_type]

    if handler.needs_project and project_id is None and project_name is None:
        err = EntityArgValidationError(
            f"read({entity_type!r}) requires project scope. Pass project_id or "
            f"project_name, or paste the {entity_type}'s Opik link/URI as the id — "
            f"e.g. read('{entity_type}', '<{entity_type}_id>', project_id='<uuid>')."
        )
        raise ToolError(str(err)) from err

    # Entity-specific kwargs reach the fetcher only when its registry entry
    # declares them — same gate as ``list``, so a kwarg meant for one entity is
    # dropped rather than crashing another's fetcher.
    extra = {
        key: value
        for key, value in entity_kwargs.items()
        if value is not None and key in handler.read_optional_kwargs
    }
    if since is not None or until is not None:
        # Same since/until vocabulary as ``list``. Which entities take a window,
        # and in which shape, is declared on the registry entry — a Diagnostics
        # issue wants whole UTC report days, a project's metrics want instants.
        window = handler.read_window
        if window is None:
            takes_window = sorted(
                name for name, entry in ENTITY_REGISTRY.items() if entry.read_window is not None
            )
            err = WindowError(
                f"since/until are not supported for read({entity_type!r}); "
                f"on read a window is taken by: {', '.join(takes_window)}."
            )
            raise ToolError(str(err)) from err
        # One clock reading for the whole window. A relative bound resolves
        # against "now", and if the end were later measured from a second
        # reading, `since="30d"` would intermittently span 30 days and a
        # second — seen live, one call in a few crossing a second boundary.
        now = datetime.now(UTC)
        try:
            from_time, to_time = resolve_window(since, until, now=now)
        except WindowError as e:
            raise ToolError(str(e)) from e
        if not window.day_truncated and to_time is None:
            # An instant window is always closed: an open end means "now", and
            # it has to be the same "now" the start was measured from. A
            # day-keyed window is left open on purpose — the Diagnostics page
            # defaults to all-time, and closing it here would silently bound it.
            to_time = format_instant(now)
        if from_time is not None:
            extra[window.start_kwarg] = from_time[:10] if window.day_truncated else from_time
        if to_time is not None:
            extra[window.end_kwarg] = to_time[:10] if window.day_truncated else to_time

    resolved_settings = settings or get_settings()
    # A read can be several backend calls (a trace and its spans, a project and
    # its metrics), so the connection is owned for the span of this call and
    # every leg reuses it.
    async with client_for_call(resolved_settings, client) as opik:
        data = await _fetch_with_name_lookup(
            handler, opik, id, project_id=project_id, project_name=project_name, extra=extra
        )
        if handler.link_fn is not None:
            # UI links are session facts (UI base, workspace), so they are attached
            # here rather than inside the fetcher, and before compression so every
            # tier can decide what to keep.
            data.update(handler.link_fn(resolved_settings, data))
        # Underscore-prefixed keys are a fetcher's private hand-off to link_fn
        # (e.g. the project an issue was read under); they are never the agent's.
        for private_key in [key for key in data if key.startswith("_")]:
            del data[private_key]

        compressed_text, tier = compress_for(handler, data, max_tokens)
        full_json = compact_json(data)
        full_tokens = estimate_tokens(full_json)
        returned_tokens = estimate_tokens(compressed_text)
        header = size_header(entity_type, id, tier, returned_tokens, full_tokens)
        return f"{header}\n{compressed_text}"


async def _fetch_with_name_lookup(
    handler: EntityHandler,
    client: OpikReadClient,
    entity_id: str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve name → id when the input doesn't look like a UUID.

    For ``id_only`` entities (trace, span, …) we skip the lookup and
    fetch directly — saves a round-trip on the common case. For nameable
    entities we hit the search endpoint, then disambiguate:

    - 0 candidates → fall through with the raw input (lets the user
      provide UUIDs that don't match our regex without being blocked).
    - 1 candidate  → resolve to that id, fetch.
    - >1 candidates → raise a disambiguation error listing the matches.
    """
    if not handler.id_only and handler.search_by_name_fn is not None and not _is_uuid(entity_id):
        try:
            candidates = await handler.search_by_name_fn(client, entity_id)
        except Exception as e:
            logger.debug("name search failed for %s: %s", handler.entity_type, e)
            candidates = []
        if len(candidates) == 1:
            entity_id = candidates[0]["id"]
        elif len(candidates) > 1:
            err = EntityArgValidationError(
                _format_ambiguous(handler.entity_type, entity_id, candidates)
            )
            raise ToolError(str(err)) from err
        # 0 candidates: fall through with the raw id; the fetch call below
        # will 404 with a clear message if it really doesn't exist.

    extra = extra or {}
    try:
        if handler.needs_project:
            return await handler.fetch_fn(
                client, entity_id, project_id=project_id, project_name=project_name, **extra
            )
        return await handler.fetch_fn(client, entity_id, **extra)
    except EntityArgValidationError as e:
        # A fetcher may reject its own scope (e.g. a project_name that resolves
        # to no or several projects). Same typed cause as the tool's own
        # argument checks, so analytics buckets it as validation/400.
        raise ToolError(str(e)) from e
    except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as e:
        raise ToolError(_format_client_error(handler.entity_type, entity_id, e)) from e


__all__ = ["run_read"]
