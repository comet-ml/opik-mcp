"""What each server mode shows of the entity table.

The registry holds every entity; a handler's ``modes`` says which modes show
it. Everything an agent is told or refused with is read through these views,
so a mode's refusal never names an entity that mode hides.
"""

from __future__ import annotations

from opik_mcp.cost_intelligence import DEFAULT_MODE, Mode
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.registry import ENTITY_REGISTRY, VOCABULARIES
from opik_mcp.read_list.unsupported import unsupported_fetch

#: List arguments only hidden types take; a mode that hides them refuses them.
MODE_HIDDEN_LIST_ARGS: tuple[str, ...] = ("dataset_id", "experiment_ids", "prompt_id", "status")


def hidden_list_args(mode: Mode) -> tuple[str, ...]:
    return () if mode == DEFAULT_MODE else MODE_HIDDEN_LIST_ARGS


def visible_handler(entity_type: str, mode: Mode) -> EntityHandler | None:
    """The handler for ``entity_type``, or ``None`` when the mode hides or lacks it."""
    handler = ENTITY_REGISTRY.get(entity_type)
    return handler if handler is not None and mode in handler.modes else None


def readable_types(mode: Mode) -> tuple[str, ...]:
    return tuple(
        t
        for t, h in ENTITY_REGISTRY.items()
        if mode in h.modes and h.fetch_fn is not unsupported_fetch
    )


def listable_types(mode: Mode) -> tuple[str, ...]:
    return tuple(t for t, h in ENTITY_REGISTRY.items() if mode in h.modes and h.lists)


def sortable_types(mode: Mode) -> tuple[str, ...]:
    return tuple(
        v.name
        for v in VOCABULARIES.values()
        if v.sort_fields and visible_handler(v.entity_type, mode) is not None
    )


def filterable_types(mode: Mode) -> tuple[str, ...]:
    return tuple(
        v.name
        for v in VOCABULARIES.values()
        if v.filter_fields and v.mode_of is None and visible_handler(v.entity_type, mode)
    )


def windowed_types(mode: Mode) -> tuple[str, ...]:
    return tuple(t for t, h in ENTITY_REGISTRY.items() if mode in h.modes and h.is_windowed)


def day_windowed_types(mode: Mode) -> tuple[str, ...]:
    """The types whose backend takes a window as whole UTC days."""
    return tuple(
        t
        for t, h in ENTITY_REGISTRY.items()
        if mode in h.modes and "from_date" in h.list_optional_kwargs
    )


def list_schema_keys(mode: Mode) -> tuple[str, ...]:
    """The ``list.<entity>`` keys ``schema`` answers for in this mode."""
    return (
        # Every vocabulary, not only every entity type: a dataset item filtered
        # under its dataset and the same item filtered with runs attached are
        # two field tables, and each has to be answerable on its own.
        *(
            f"list.{v.name}"
            for v in VOCABULARIES.values()
            if v.filter_fields and visible_handler(v.entity_type, mode) is not None
        ),
        # An entity whose reference is not a field table answers its own.
        *(
            f"list.{t}"
            for t, h in ENTITY_REGISTRY.items()
            if mode in h.modes and h.reference_fn is not None
        ),
    )


__all__ = [
    "MODE_HIDDEN_LIST_ARGS",
    "day_windowed_types",
    "filterable_types",
    "hidden_list_args",
    "list_schema_keys",
    "listable_types",
    "readable_types",
    "sortable_types",
    "visible_handler",
    "windowed_types",
]
