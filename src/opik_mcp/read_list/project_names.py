"""Which score names and usage keys a project has recorded.

Shared because two entities need the same fact for different reasons. The
project overview lists them as the vocabulary a caller writes its next
question in; a metric series checks a ``series`` name against them before
concluding that an empty chart means a quiet window. Neither owns the fact,
and one entity importing the other's namespace to get it is the coupling the
layout exists to prevent.

The caps live here too. They are a display policy — how many names is it
reasonable to put in front of a caller — and both readers want the same
answer, whether the names are going into an overview or into a refusal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.decorations import BLOCK_ERRORS, DEADLINE_SECONDS

Fetcher = Callable[[OpikReadClient, str], Awaitable[list[str]]]

SCORE_NAMES_CAP: Final = 25
"""Enough to see a project's real vocabulary; a judge rule per metric plus
per-author scores can run to hundreds, which would swallow the read."""

USAGE_KEYS_CAP: Final = 15
"""Standard token keys number about six. The cap is for a project whose
instrumentation invents its own."""

_LOOKUP_ERRORS: Final[tuple[type[BaseException], ...]] = (TimeoutError, *BLOCK_ERRORS)
"""What a name lookup is allowed to fail with — the same set a decoration
survives, plus the deadline. Named rather than unpacked inline so the type is
stated."""


def named(rows: Any) -> list[str]:
    """The ``name`` of every well-formed row, in order."""
    if not isinstance(rows, list):
        return []
    return [
        row["name"] for row in rows if isinstance(row, dict) and isinstance(row.get("name"), str)
    ]


async def fetch_score_names(client: OpikReadClient, project_id: str) -> list[str]:
    """``{scores: [{name}]}`` — rows, not strings, and no paging."""
    body = await client.list_project_score_names(project_id)
    return named(body.get("scores"))


async def fetch_usage_keys(client: OpikReadClient, project_id: str) -> list[str]:
    """``{names: [str]}`` — bare strings, unlike every other name endpoint."""
    body = await client.list_project_token_usage_names(project_id)
    raw = body.get("names")
    return [key for key in raw if isinstance(key, str)] if isinstance(raw, list) else []


_FETCH: Final[dict[str, Fetcher]] = {"score": fetch_score_names, "usage": fetch_usage_keys}
"""How each kind of name is asked for and unpacked, once.

The two endpoints answer in different shapes, and each shape was being
unpacked in three places. One entry each, and the shape stops being a fact
three files have to agree on.
"""


async def recorded(client: OpikReadClient, project_id: str, *, kind: str) -> list[str] | None:
    """Every name of one kind in a project, uncapped and undecorated.

    The overview's vocabulary block caps and captions its lists for a reader;
    a caller checking a name the agent supplied needs the whole list and none
    of the furniture. ``None`` means the lookup itself failed — which a
    checker must not confuse with "the name is not there", or a slow endpoint
    would turn into a refusal of a perfectly good name.
    """
    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            return await _FETCH[kind](client, project_id)
    except _LOOKUP_ERRORS:
        return None


__all__ = [
    "SCORE_NAMES_CAP",
    "USAGE_KEYS_CAP",
    "fetch_score_names",
    "fetch_usage_keys",
    "named",
    "recorded",
]
