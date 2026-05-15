"""Registry-level invariants — readable/listable surfaces, composite shapes."""

from __future__ import annotations

from opik_mcp.read_list.registry import (
    ENTITY_REGISTRY,
    LISTABLE_TYPES,
    READABLE_TYPES,
)


def test_list_only_entities_excluded_from_readable() -> None:
    """test_suite_item and prompt_version are sub-collections — they can only be
    listed under a parent, never fetched by their own id through the read tool.
    A regression that adds them to READABLE_TYPES would create an unusable code
    path (no get_* endpoint exists on the client)."""
    assert "test_suite_item" not in READABLE_TYPES
    assert "prompt_version" not in READABLE_TYPES


def test_singletons_with_no_list_endpoint_excluded_from_listable() -> None:
    """``span`` is fetchable by id but not enumerable — verify the dispatcher knows."""
    assert "span" not in LISTABLE_TYPES


def test_id_only_flag_set_for_trace_span_prompt_version_test_suite_item() -> None:
    assert ENTITY_REGISTRY["trace"].id_only
    assert ENTITY_REGISTRY["span"].id_only
    assert ENTITY_REGISTRY["test_suite_item"].id_only
    assert ENTITY_REGISTRY["prompt_version"].id_only


def test_nameable_entities_have_search_fn() -> None:
    for entity_type in ("project", "experiment", "prompt", "test_suite"):
        assert ENTITY_REGISTRY[entity_type].search_by_name_fn is not None


def test_id_only_entities_have_no_search_fn() -> None:
    for entity_type in ("trace", "span"):
        assert ENTITY_REGISTRY[entity_type].search_by_name_fn is None


def test_project_scoped_lists_declare_required_kwarg() -> None:
    assert ENTITY_REGISTRY["trace"].list_required_kwargs == ("project_id",)
    assert ENTITY_REGISTRY["test_suite_item"].list_required_kwargs == ("test_suite_id",)
    assert ENTITY_REGISTRY["prompt_version"].list_required_kwargs == ("prompt_id",)
