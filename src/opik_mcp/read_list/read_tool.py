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
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikReadClient,
    OpikServerError,
    OpikValidationError,
    make_opik_client,
)
from opik_mcp.read_list.compression import compact_json, estimate_tokens, size_header
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.registry import (
    ENTITY_REGISTRY,
    READABLE_TYPES,
    EntityHandler,
    compress_for,
)
from opik_mcp.read_list.uri import InvalidURI, looks_like_thread_url, looks_like_uri
from opik_mcp.read_list.uri import parse as parse_uri

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
    if isinstance(exc, OpikAuthError):
        return (
            f"Permission denied fetching {entity_type} '{entity_id}'. "
            "The current workspace may not have access to this entity. "
            f"Detail: {exc}"
        )
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


@dataclass(frozen=True)
class FetchedEntity:
    """What ``fetch_entity`` resolved, before anything is rendered.

    Callers that render for the model compress ``data``; callers that render for
    a human (the MCP App) use it as-is. Both get the same resolved identifiers,
    which is the point: a pasted link or an entity name must mean the same thing
    on both paths.
    """

    entity_type: str
    entity_id: str
    project_id: str | None
    project_name: str | None
    handler: EntityHandler
    data: dict[str, Any]


async def fetch_entity(
    entity_type: str,
    id: str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
    settings: Settings | None = None,
    client: OpikReadClient | None = None,
) -> FetchedEntity:
    """Resolve a reference and fetch it: URI parse → registry lookup → project
    gate → UUID-vs-name branch → fetch.

    Everything ``read`` accepts as an ``id`` is accepted here — a UUID, an entity
    name, an ``opik://`` URI, or a pasted Opik thread link. Errors surface as
    ``ToolError`` so the host LLM gets the structured guidance.
    """
    # Accept ``opik://…`` URIs and pasted web thread links as id input. When the
    # URI encodes its own entity_type we trust it and override the explicit
    # argument — that way the agent can paste a URI into either slot. A parsed
    # thread URI/link also carries the project, which overrides the explicit arg.
    if looks_like_uri(id) or looks_like_thread_url(id):
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
            f"read({entity_type!r}) requires project scope. Pass the full thread "
            f"link/URI, or a project_id — e.g. "
            f"read('{entity_type}', '<{entity_type}_id>', project_id='<uuid>')."
        )
        raise ToolError(str(err)) from err

    opik = client if client is not None else make_opik_client(settings or get_settings())
    resolved_id, data = await _fetch_with_name_lookup(
        handler, opik, id, project_id=project_id, project_name=project_name
    )
    return FetchedEntity(
        entity_type=entity_type,
        entity_id=resolved_id,
        project_id=project_id,
        project_name=project_name,
        handler=handler,
        data=data,
    )


async def run_read(
    entity_type: str,
    id: str,
    *,
    max_tokens: int | None = None,
    project_id: str | None = None,
    project_name: str | None = None,
    settings: Settings | None = None,
    client: OpikReadClient | None = None,
) -> str:
    """Read tool entrypoint — ``fetch_entity`` plus compression to a token budget.

    See ``server.py`` for the registered tool.
    """
    fetched = await fetch_entity(
        entity_type,
        id,
        project_id=project_id,
        project_name=project_name,
        settings=settings,
        client=client,
    )
    compressed_text, tier = compress_for(fetched.handler, fetched.data, max_tokens)
    full_json = compact_json(fetched.data)
    full_tokens = estimate_tokens(full_json)
    returned_tokens = estimate_tokens(compressed_text)
    header = size_header(fetched.entity_type, fetched.entity_id, tier, returned_tokens, full_tokens)
    return f"{header}\n{compressed_text}"


async def _fetch_with_name_lookup(
    handler: EntityHandler,
    client: OpikReadClient,
    entity_id: str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve name → id when the input doesn't look like a UUID, and fetch.

    Returns the id actually used, not just the payload: a caller that resolved a
    name or a link needs to know what it ended up reading.

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

    try:
        if handler.needs_project:
            data = await handler.fetch_fn(
                client, entity_id, project_id=project_id, project_name=project_name
            )
        else:
            data = await handler.fetch_fn(client, entity_id)
        return entity_id, data
    except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as e:
        raise ToolError(_format_client_error(handler.entity_type, entity_id, e)) from e


__all__ = ["run_read"]
