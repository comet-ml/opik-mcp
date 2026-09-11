"""A stand-in for the one handler slot an entity can legitimately not fill.

``fetch_fn`` is required by the contract, so a list-only entity needs
something to put there. It raises if it is ever reached, since reaching it
means the read tool lost track of which types it can answer for.

There were two of these. The second existed so that an entity answering
``list`` through its own runner would still count as listable; asking the
handler whether it *lists* instead of whether it has a ``list_fn`` retired it,
along with the list function it declared and never called.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikReadClient


async def unsupported_fetch(_client: OpikReadClient, _entity_id: str) -> dict[str, Any]:
    """Sentinel for list-only entities. The read tool raises before calling this."""
    raise NotImplementedError(
        "This entity is list-only — use list() with the parent id, or read the parent entity."
    )


__all__ = ["unsupported_fetch"]
