"""Stand-ins for the two handler slots an entity can legitimately not fill.

``fetch_fn`` is required by the contract, so a list-only entity needs
something to put there; and one entity is handled whole by its own runner
before the registry is consulted. Both raise if they are ever reached, since
reaching them means the dispatcher lost track of which path it was on.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient


async def unsupported_fetch(_client: OpikReadClient, _entity_id: str) -> dict[str, Any]:
    """Sentinel for list-only entities. The read tool raises before calling this."""
    raise NotImplementedError(
        "This entity is list-only — use list() with the parent id, or read the parent entity."
    )


async def delegated_elsewhere(_client: OpikListClient, **_kw: Any) -> dict[str, Any]:
    """Sentinel for an entity the list tool hands off before reaching the registry."""
    raise NotImplementedError(
        "This entity is handled by its own runner; the list tool delegates before here."
    )


__all__ = ["delegated_elsewhere", "unsupported_fetch"]
