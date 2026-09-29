"""What a feature set shows of the entity table, and what a workspace without it refuses.

The default views are what every host loads today, so they are pinned to the
sets they had before features existed.
"""

from __future__ import annotations

from typing import cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.cost_intelligence import AI_SPEND_FEATURE, WORKSPACE_PREFIX
from opik_mcp.read_list import registry
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.read_list.reference import LIST_SCHEMA_KEYS
from opik_mcp.read_list.visibility import (
    added_listable,
    added_readable,
    added_schema_keys,
    filterable_types,
    list_schema_keys,
    listable_types,
    readable_types,
    sortable_types,
    visible_handler,
    windowed_types,
)
from opik_mcp.writes.schema_tool import run_schema
from tests.cost_intelligence.build import FAKE_TYPE, add_fake_feature_entity
from tests.factories import make_settings

pytestmark = pytest.mark.anyio

NONE = frozenset[str]()
SPEND = frozenset({AI_SPEND_FEATURE})
SPEND_SETTINGS = make_settings(opik_workspace=f"{WORKSPACE_PREFIX}org__", opik_api_key="k")
DEFAULT_SETTINGS = make_settings(opik_workspace="team", opik_api_key="k")
UUID = "0190a3c4-1111-7000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _NoBackend:
    """A refusal must come before any backend call; any attribute use fails."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"backend touched: {name}")


NO_BACKEND = cast("OpikReadClient", _NoBackend())


def test_the_default_views_are_what_they_were_before_features_existed() -> None:
    assert readable_types(NONE) == registry.READABLE_TYPES
    assert listable_types(NONE) == registry.LISTABLE_TYPES
    assert sortable_types(NONE) == registry.SORTABLE_TYPES
    assert filterable_types(NONE) == registry.FILTERABLE_TYPES
    assert windowed_types(NONE) == registry.WINDOWED_TYPES
    assert list_schema_keys(NONE) == LIST_SCHEMA_KEYS
    assert registry.READABLE_TYPES == (
        "project",
        "trace",
        "span",
        "thread",
        "experiment",
        "dataset",
        "dataset_item",
        "prompt",
        "agent_insights_issue",
    )
    assert registry.LISTABLE_TYPES == (
        "project",
        "trace",
        "span",
        "thread",
        "experiment",
        "dataset",
        "dataset_item",
        "prompt",
        "prompt_version",
        "project_metric",
        "score_name",
        "online_rule",
        "agent_insights_issue",
    )
    assert LIST_SCHEMA_KEYS == (
        "list.trace",
        "list.span",
        "list.thread",
        "list.experiment",
        "list.dataset_item",
        "list.dataset_item_case",
        "list.project_metric",
    )


def test_a_feature_set_with_no_entity_of_its_own_adds_nothing() -> None:
    assert added_readable(SPEND) == []
    assert added_listable(SPEND) == []
    assert added_schema_keys(SPEND) == []
    assert readable_types(SPEND) == registry.READABLE_TYPES


def test_a_feature_entity_is_added_by_its_feature_and_by_no_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_fake_feature_entity(monkeypatch)
    assert added_readable(SPEND) == [FAKE_TYPE]
    assert added_listable(SPEND) == [FAKE_TYPE]
    assert added_schema_keys(SPEND) == [f"list.{FAKE_TYPE}"]
    for views in (readable_types, listable_types, filterable_types):
        assert FAKE_TYPE in views(SPEND), views.__name__
        assert FAKE_TYPE not in views(NONE), views.__name__
        assert FAKE_TYPE not in views(frozenset({"other"})), views.__name__
    assert f"list.{FAKE_TYPE}" not in list_schema_keys(NONE)
    assert visible_handler(FAKE_TYPE, SPEND) is not None
    assert visible_handler(FAKE_TYPE, NONE) is None


def test_the_default_registry_tuples_leave_a_feature_entity_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_fake_feature_entity(monkeypatch)
    assert registry.ENTITY_REGISTRY[FAKE_TYPE].feature == AI_SPEND_FEATURE
    assert FAKE_TYPE in readable_types(SPEND)
    assert FAKE_TYPE not in registry.READABLE_TYPES
    assert FAKE_TYPE not in registry.LISTABLE_TYPES


async def test_read_refuses_a_feature_type_by_name_without_the_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_fake_feature_entity(monkeypatch)
    with pytest.raises(ToolError) as exc:
        await run_read(FAKE_TYPE, UUID, settings=DEFAULT_SETTINGS, client=NO_BACKEND)
    text = str(exc.value)
    assert f"Invalid entity_type {FAKE_TYPE!r}. Readable types:" in text
    assert "list-only" not in text
    assert text.count(FAKE_TYPE) == 1


async def test_list_refuses_a_feature_type_by_name_without_the_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_fake_feature_entity(monkeypatch)
    with pytest.raises(ToolError) as exc:
        await run_list(FAKE_TYPE, settings=DEFAULT_SETTINGS, client=NO_BACKEND)
    text = str(exc.value)
    assert f"Cannot list {FAKE_TYPE!r}. Listable types:" in text
    assert text.count(FAKE_TYPE) == 1


async def test_read_and_list_reach_a_feature_type_with_the_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_fake_feature_entity(monkeypatch)
    assert UUID in await run_read(FAKE_TYPE, UUID, settings=SPEND_SETTINGS, client=NO_BACKEND)
    assert FAKE_TYPE in await run_list(FAKE_TYPE, settings=SPEND_SETTINGS, client=NO_BACKEND)


def test_schema_answers_a_feature_list_key_only_with_the_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_fake_feature_entity(monkeypatch)
    key = f"list.{FAKE_TYPE}"
    monkeypatch.setattr("opik_mcp.writes.schema_tool.get_settings", lambda: SPEND_SETTINGS)
    assert run_schema(key)
    monkeypatch.setattr("opik_mcp.writes.schema_tool.get_settings", lambda: DEFAULT_SETTINGS)
    with pytest.raises(ToolError):
        run_schema(key)


def test_a_missing_record_offers_the_listing_the_features_have() -> None:
    from opik_mcp.client.base import OpikNotFoundError
    from opik_mcp.read_list.read_tool import _format_client_error

    text = _format_client_error("trace", "x", OpikNotFoundError("m"), NONE)
    assert "find it with list('trace'" in text
