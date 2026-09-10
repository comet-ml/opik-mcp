"""The table ``read`` and ``list`` dispatch over.

One row per entity type, and nothing about any entity: each one declares its
own handler in ``entities/`` and this collects them. Adding an entity is a
module (or a package, when it needs more than one file) plus an import here.

What a row can declare is in ``handler.py``. The two behaviours that read the
table rather than a row live here: the aliases an ``entity_type`` is resolved
through, and the compression an entity did not override.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.read_list.compression import CompressionTier
from opik_mcp.read_list.compression import compress as generic_compress
from opik_mcp.read_list.entities import (
    agent_insights_issue,
    experiment,
    online_rule,
    project,
    project_metric,
    prompt,
    score_name,
    span,
    test_suite,
    thread,
    trace,
)
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.unsupported import unsupported_fetch

ENTITY_REGISTRY: dict[str, EntityHandler] = {
    handler.entity_type: handler
    for handler in (
        project.HANDLER,
        trace.HANDLER,
        span.HANDLER,
        thread.HANDLER,
        test_suite.HANDLER,
        test_suite.ITEM_HANDLER,
        experiment.HANDLER,
        prompt.HANDLER,
        prompt.VERSION_HANDLER,
        project_metric.HANDLER,
        score_name.HANDLER,
        online_rule.HANDLER,
        agent_insights_issue.HANDLER,
    )
}


#: Short names accepted for an entity type, resolved before the registry
#: lookup. Deliberately not advertised in the tools' ``entity_type`` enum: the
#: enum is the closed set an agent should choose from, and listing a type twice
#: under two names invites the question of which is real. This is a safety net
#: for the guess an agent makes anyway — ``agent_insights_issue`` is a mouthful,
#: and "issue" is what the UI calls it.
ENTITY_ALIASES: dict[str, str] = {"issue": "agent_insights_issue"}


def resolve_entity_type(entity_type: str) -> str:
    """The registry name for ``entity_type``, mapping any alias."""
    return ENTITY_ALIASES.get(entity_type, entity_type)


READABLE_TYPES: tuple[str, ...] = tuple(
    t for t, h in ENTITY_REGISTRY.items() if h.fetch_fn is not unsupported_fetch
)
LISTABLE_TYPES: tuple[str, ...] = tuple(t for t, h in ENTITY_REGISTRY.items() if h.lists)


def compress_for(
    handler: EntityHandler,
    data: dict[str, Any],
    max_tokens: int | None,
) -> tuple[str, CompressionTier]:
    if handler.compress_fn is not None:
        return handler.compress_fn(data, max_tokens)
    return generic_compress(data, entity_type=handler.entity_type, max_tokens=max_tokens)


__all__ = [
    "ENTITY_ALIASES",
    "ENTITY_REGISTRY",
    "LISTABLE_TYPES",
    "READABLE_TYPES",
    "compress_for",
    "resolve_entity_type",
]
