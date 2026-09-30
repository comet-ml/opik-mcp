"""What a workspace's enabled features show of the entity table.

The registry holds every entity; a handler's ``feature`` says which feature
turns it on. Everything an agent is told or refused with is read through these
views, so a workspace without a feature never hears of the entities behind it.
"""

from __future__ import annotations

from typing import Final

from opik_mcp.features.toggles import NO_FEATURES, FeatureToggles
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.registry import ENTITY_REGISTRY, VOCABULARIES
from opik_mcp.read_list.unsupported import unsupported_fetch


def _is_shown(handler: EntityHandler, toggles: FeatureToggles) -> bool:
    return handler.shown_when is None or handler.shown_when(toggles)


def visible_handler(entity_type: str, toggles: FeatureToggles) -> EntityHandler | None:
    """The handler for ``entity_type``, or ``None`` when it is unknown or its feature is off."""
    handler = ENTITY_REGISTRY.get(entity_type)
    return handler if handler is not None and _is_shown(handler, toggles) else None


def readable_types(toggles: FeatureToggles) -> tuple[str, ...]:
    return tuple(
        t
        for t, h in ENTITY_REGISTRY.items()
        if _is_shown(h, toggles) and h.fetch_fn is not unsupported_fetch
    )


def listable_types(toggles: FeatureToggles) -> tuple[str, ...]:
    return tuple(t for t, h in ENTITY_REGISTRY.items() if _is_shown(h, toggles) and h.lists)


def sortable_types(toggles: FeatureToggles) -> tuple[str, ...]:
    return tuple(
        v.name
        for v in VOCABULARIES.values()
        if v.sort_fields and visible_handler(v.entity_type, toggles) is not None
    )


def filterable_types(toggles: FeatureToggles) -> tuple[str, ...]:
    return tuple(
        v.name
        for v in VOCABULARIES.values()
        if v.filter_fields and v.mode_of is None and visible_handler(v.entity_type, toggles)
    )


def windowed_types(toggles: FeatureToggles) -> tuple[str, ...]:
    return tuple(t for t, h in ENTITY_REGISTRY.items() if _is_shown(h, toggles) and h.is_windowed)


def day_windowed_types(toggles: FeatureToggles) -> tuple[str, ...]:
    """The types whose backend takes a window as whole UTC days."""
    return tuple(
        t
        for t, h in ENTITY_REGISTRY.items()
        if _is_shown(h, toggles) and "from_date" in h.list_optional_kwargs
    )


def list_schema_keys(toggles: FeatureToggles) -> tuple[str, ...]:
    """The ``list.<entity>`` keys ``schema`` answers for with these features on."""
    return (
        # Every vocabulary, not only every entity type: a dataset item filtered
        # under its dataset and the same item filtered with runs attached are
        # two field tables, and each has to be answerable on its own.
        *(
            f"list.{v.name}"
            for v in VOCABULARIES.values()
            if v.filter_fields and visible_handler(v.entity_type, toggles) is not None
        ),
        # An entity whose reference is not a field table answers its own.
        *(
            f"list.{t}"
            for t, h in ENTITY_REGISTRY.items()
            if _is_shown(h, toggles) and h.reference_fn is not None
        ),
    )


#: What a workspace with no feature on shows. The tool signatures advertise these,
#: so the base enum and the call-time view cannot drift apart.
DEFAULT_READABLE_TYPES: Final[tuple[str, ...]] = readable_types(NO_FEATURES)
DEFAULT_LISTABLE_TYPES: Final[tuple[str, ...]] = listable_types(NO_FEATURES)

__all__ = [
    "DEFAULT_LISTABLE_TYPES",
    "DEFAULT_READABLE_TYPES",
    "day_windowed_types",
    "filterable_types",
    "list_schema_keys",
    "listable_types",
    "readable_types",
    "sortable_types",
    "visible_handler",
    "windowed_types",
]
