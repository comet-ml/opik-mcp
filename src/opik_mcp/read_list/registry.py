"""The table ``read`` and ``list`` dispatch over.

One row per entity type, and nothing about any entity: each one declares its
own handler in ``entities/`` and this collects them. Adding an entity is a
module (or a package, when it needs more than one file) plus an import here.

What a row can declare is in ``handler.py``. The two behaviours that read the
table rather than a row live here: the aliases an ``entity_type`` is resolved
through.
"""

from __future__ import annotations

from opik_mcp.read_list.entities import (
    agent_insights_issue,
    dataset,
    experiment,
    online_rule,
    project,
    project_metric,
    prompt,
    score_name,
    span,
    thread,
    trace,
)
from opik_mcp.read_list.handler import EntityHandler, Vocabulary
from opik_mcp.read_list.unsupported import unsupported_fetch

ENTITY_REGISTRY: dict[str, EntityHandler] = {
    handler.entity_type: handler
    for handler in (
        project.HANDLER,
        trace.HANDLER,
        span.HANDLER,
        thread.HANDLER,
        experiment.HANDLER,
        dataset.HANDLER,
        dataset.ITEM_HANDLER,
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
#: and "issue" is what the UI calls it; ``test_suite`` is what this tool called
#: the dataset before the rename, and callers still reach for it.
ENTITY_ALIASES: dict[str, str] = {
    "issue": "agent_insights_issue",
    "test_suite": "dataset",
    "test_suite_item": "dataset_item",
    # The field table behind ``schema("list.dataset_item_case")``
    # (``oql.VOCABULARY_MODES``). Refusals name the entity a caller typed, but
    # the reference pointer beside them names this key — and an agent that has
    # just read a reference is the likeliest caller to type its name back.
    "dataset_item_case": "dataset_item",
}


def resolve_entity_type(entity_type: str) -> str:
    """The registry name for ``entity_type``, mapping any alias."""
    return ENTITY_ALIASES.get(entity_type, entity_type)


READABLE_TYPES: tuple[str, ...] = tuple(
    t for t, h in ENTITY_REGISTRY.items() if h.fetch_fn is not unsupported_fetch
)
LISTABLE_TYPES: tuple[str, ...] = tuple(t for t, h in ENTITY_REGISTRY.items() if h.lists)

#: Every field table a ``list`` call can be checked against, by name, in
#: registry order: ``schema("list.…")`` keys and refusals list them this way.
VOCABULARIES: dict[str, Vocabulary] = {
    vocabulary.name: vocabulary
    for handler in ENTITY_REGISTRY.values()
    for vocabulary in handler.vocabularies
}
SORTABLE_TYPES: tuple[str, ...] = tuple(v.name for v in VOCABULARIES.values() if v.sort_fields)

__all__ = [
    "ENTITY_ALIASES",
    "ENTITY_REGISTRY",
    "LISTABLE_TYPES",
    "READABLE_TYPES",
    "SORTABLE_TYPES",
    "VOCABULARIES",
    "resolve_entity_type",
]
