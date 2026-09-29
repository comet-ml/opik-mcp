"""What a workspace's enabled features show of the entity table.

The registry holds every entity; a handler's ``feature`` says which feature
turns it on. Everything an agent is told or refused with is read through these
views, so a workspace without a feature never hears of the entities behind it.
"""

from __future__ import annotations

from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.registry import ENTITY_REGISTRY, VOCABULARIES
from opik_mcp.read_list.unsupported import unsupported_fetch


def _is_on(handler: EntityHandler, features: frozenset[str]) -> bool:
    return handler.feature is None or handler.feature in features


def visible_handler(entity_type: str, features: frozenset[str]) -> EntityHandler | None:
    """The handler for ``entity_type``, or ``None`` when it is unknown or its feature is off."""
    handler = ENTITY_REGISTRY.get(entity_type)
    return handler if handler is not None and _is_on(handler, features) else None


def readable_types(features: frozenset[str]) -> tuple[str, ...]:
    return tuple(
        t
        for t, h in ENTITY_REGISTRY.items()
        if _is_on(h, features) and h.fetch_fn is not unsupported_fetch
    )


def listable_types(features: frozenset[str]) -> tuple[str, ...]:
    return tuple(t for t, h in ENTITY_REGISTRY.items() if _is_on(h, features) and h.lists)


def sortable_types(features: frozenset[str]) -> tuple[str, ...]:
    return tuple(
        v.name
        for v in VOCABULARIES.values()
        if v.sort_fields and visible_handler(v.entity_type, features) is not None
    )


def filterable_types(features: frozenset[str]) -> tuple[str, ...]:
    return tuple(
        v.name
        for v in VOCABULARIES.values()
        if v.filter_fields and v.mode_of is None and visible_handler(v.entity_type, features)
    )


def windowed_types(features: frozenset[str]) -> tuple[str, ...]:
    return tuple(t for t, h in ENTITY_REGISTRY.items() if _is_on(h, features) and h.is_windowed)


def day_windowed_types(features: frozenset[str]) -> tuple[str, ...]:
    """The types whose backend takes a window as whole UTC days."""
    return tuple(
        t
        for t, h in ENTITY_REGISTRY.items()
        if _is_on(h, features) and "from_date" in h.list_optional_kwargs
    )


def list_schema_keys(features: frozenset[str]) -> tuple[str, ...]:
    """The ``list.<entity>`` keys ``schema`` answers for with these features on."""
    return (
        # Every vocabulary, not only every entity type: a dataset item filtered
        # under its dataset and the same item filtered with runs attached are
        # two field tables, and each has to be answerable on its own.
        *(
            f"list.{v.name}"
            for v in VOCABULARIES.values()
            if v.filter_fields and visible_handler(v.entity_type, features) is not None
        ),
        # An entity whose reference is not a field table answers its own.
        *(
            f"list.{t}"
            for t, h in ENTITY_REGISTRY.items()
            if _is_on(h, features) and h.reference_fn is not None
        ),
    )


def added_readable(features: frozenset[str]) -> list[str]:
    """The readable types these features add over the default."""
    return sorted(set(readable_types(features)) - set(readable_types(frozenset())))


def added_listable(features: frozenset[str]) -> list[str]:
    return sorted(set(listable_types(features)) - set(listable_types(frozenset())))


def added_schema_keys(features: frozenset[str]) -> list[str]:
    return sorted(set(list_schema_keys(features)) - set(list_schema_keys(frozenset())))


__all__ = [
    "added_listable",
    "added_readable",
    "added_schema_keys",
    "day_windowed_types",
    "filterable_types",
    "list_schema_keys",
    "listable_types",
    "readable_types",
    "sortable_types",
    "visible_handler",
    "windowed_types",
]
