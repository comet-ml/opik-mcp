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


def test_span_is_listable_with_project_scope() -> None:
    """``span`` is enumerable project-wide (OPIK-8283); the project requirement
    is what the ``list`` tool enforces before calling the backend."""
    assert "span" in LISTABLE_TYPES
    assert ENTITY_REGISTRY["span"].list_required_kwargs == ("project_id",)


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
    assert ENTITY_REGISTRY["thread"].list_required_kwargs == ("project_id",)


def test_thread_is_both_readable_and_listable() -> None:
    assert "thread" in READABLE_TYPES
    assert "thread" in LISTABLE_TYPES


def test_thread_needs_project_and_is_id_only() -> None:
    handler = ENTITY_REGISTRY["thread"]
    assert handler.needs_project is True
    assert handler.id_only is True
    assert handler.search_by_name_fn is None


def test_needs_project_is_declared_by_project_scoped_reads_only() -> None:
    """thread and agent_insights_issue need project scope on read — every other
    fetcher is (client, id)."""
    for entity_type, handler in ENTITY_REGISTRY.items():
        assert handler.needs_project is (entity_type in {"thread", "agent_insights_issue"})


def test_optional_kwargs_never_overlap_required_ones() -> None:
    """A kwarg is either required or optional for an entity, never both — the
    list tool's forwarding gate unions the two, so an overlap would hide a
    missing-parent error."""
    for handler in ENTITY_REGISTRY.values():
        assert not set(handler.list_required_kwargs) & set(handler.list_optional_kwargs)


_DECLARES_OPTIONAL_KWARGS = {
    # Diagnostics issues: which status to list, and the report-day window.
    "agent_insights_issue": {"status", "from_date", "to_date"},
}


def test_only_the_declared_entities_take_optional_kwargs() -> None:
    """Every other entity takes nothing beyond its parent id, so the gate
    must drop whatever else the caller passes. Pinned as a set rather than a
    single name so a third entity has to be added here deliberately."""
    for entity_type, handler in ENTITY_REGISTRY.items():
        expected = _DECLARES_OPTIONAL_KWARGS.get(entity_type, set())
        assert set(handler.list_optional_kwargs) == expected, entity_type
        assert handler.read_optional_kwargs == (), entity_type


def test_agent_insights_issue_is_project_scoped_and_listable() -> None:
    handler = ENTITY_REGISTRY["agent_insights_issue"]
    assert "agent_insights_issue" in LISTABLE_TYPES
    assert "agent_insights_issue" in READABLE_TYPES
    assert handler.list_required_kwargs == ("project_id",)
    assert set(handler.list_optional_kwargs) == {"status", "from_date", "to_date"}
    # The read window is declared as a window, not as loose kwargs, so its
    # shape travels with it: the Diagnostics endpoints key on whole UTC days.
    assert handler.read_window is not None
    assert (handler.read_window.start_kwarg, handler.read_window.end_kwarg) == (
        "from_date",
        "to_date",
    )
    assert handler.read_window.day_truncated is True
    assert handler.needs_project is True
    assert handler.id_only is True
    assert handler.search_by_name_fn is None
    assert handler.compress_fn is not None
