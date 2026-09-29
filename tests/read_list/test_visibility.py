"""What cost intelligence mode shows of the entity table, and what it refuses.

The default mode is what every host loads today, so its views are pinned to
the sets they had before modes existed.
"""

from __future__ import annotations

from typing import cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.cost_intelligence import COST_INTELLIGENCE_MODE, DEFAULT_MODE, WORKSPACE_PREFIX
from opik_mcp.read_list import registry
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.read_list.reference import LIST_SCHEMA_KEYS
from opik_mcp.read_list.visibility import (
    filterable_types,
    list_schema_keys,
    listable_types,
    readable_types,
    sortable_types,
    windowed_types,
)
from opik_mcp.writes.registry import WRITE_OPERATIONS
from opik_mcp.writes.schema_tool import run_schema
from tests.factories import make_settings

pytestmark = pytest.mark.anyio

SPEND = make_settings(opik_workspace=f"{WORKSPACE_PREFIX}org__", opik_api_key="k")
DEFAULT = make_settings(opik_workspace="team", opik_api_key="k")
SPEND_TYPES = ("spend_summary", "spend_lane", "spend_user", "spend_session", "spend_agent")
VISIBLE = ("project", "trace", "span", "thread", "project_metric", *SPEND_TYPES)
HIDDEN = ("experiment", "dataset", "prompt", "agent_insights_issue", "score_name", "online_rule")
_LISTABLE = (
    "Listable types: project, project_metric, span, spend_agent, spend_lane, "
    "spend_session, spend_summary, spend_user, thread, trace"
)
UUID = "0190a3c4-1111-7000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _NoBackend:
    """A refusal must come before any backend call; any attribute use fails."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"backend touched: {name}")


NO_BACKEND = cast("OpikReadClient", _NoBackend())


def _refusal(exc: pytest.ExceptionInfo[ToolError]) -> str:
    return str(exc.value)


def test_the_default_views_are_what_they_were_before_modes_existed() -> None:
    assert readable_types(DEFAULT_MODE) == registry.READABLE_TYPES
    assert listable_types(DEFAULT_MODE) == registry.LISTABLE_TYPES
    assert sortable_types(DEFAULT_MODE) == registry.SORTABLE_TYPES
    assert filterable_types(DEFAULT_MODE) == registry.FILTERABLE_TYPES
    assert windowed_types(DEFAULT_MODE) == registry.WINDOWED_TYPES
    assert list_schema_keys(DEFAULT_MODE) == LIST_SCHEMA_KEYS
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


def test_cost_intelligence_shows_the_project_data_and_spend_types_only() -> None:
    mode = COST_INTELLIGENCE_MODE
    assert set(readable_types(mode)) == {
        "project",
        "trace",
        "span",
        "thread",
        "spend_lane",
        "spend_session",
    }
    assert set(listable_types(mode)) == set(VISIBLE)
    assert set(filterable_types(mode)) == {"trace", "span", "thread", *SPEND_TYPES}
    assert set(sortable_types(mode)) == {
        "project",
        "trace",
        "span",
        "thread",
        "spend_user",
        "spend_session",
    }
    assert set(windowed_types(mode)) == {"trace", "span", "thread"}
    assert set(list_schema_keys(mode)) == {
        "list.trace",
        "list.span",
        "list.thread",
        "list.project_metric",
        *(f"list.{name}" for name in SPEND_TYPES),
    }


def test_the_registry_still_holds_every_entity() -> None:
    assert set(HIDDEN) <= set(registry.ENTITY_REGISTRY)


@pytest.mark.parametrize("entity_type", [*HIDDEN, "issue", "test_suite"])
async def test_read_refuses_a_hidden_type_and_names_only_visible_ones(entity_type: str) -> None:
    with pytest.raises(ToolError) as exc:
        await run_read(entity_type, "x", settings=SPEND, client=NO_BACKEND)
    text = _refusal(exc).replace(repr(entity_type), "")
    assert "Readable types: project, span, spend_lane, spend_session, thread, trace" in text
    assert not any(hidden in text for hidden in ("dataset", "experiment", "prompt", "issue"))


async def test_read_refuses_a_pasted_link_to_a_hidden_type() -> None:
    link = f"opik://experiments/{UUID}"
    with pytest.raises(ToolError) as exc:
        await run_read("trace", link, settings=SPEND, client=NO_BACKEND)
    assert "Readable types: project, span, spend_lane, spend_session, thread, trace" in _refusal(
        exc
    )


async def test_read_in_the_default_mode_still_reaches_the_type() -> None:
    with pytest.raises(AssertionError, match="backend touched"):
        await run_read("experiment", UUID, settings=DEFAULT, client=NO_BACKEND)


@pytest.mark.parametrize("entity_type", [*HIDDEN, "issue", "test_suite"])
async def test_list_refuses_a_hidden_type_and_names_only_visible_ones(entity_type: str) -> None:
    with pytest.raises(ToolError) as exc:
        await run_list(entity_type, settings=SPEND, client=NO_BACKEND)
    text = _refusal(exc).replace(repr(entity_type), "")
    assert _LISTABLE in text
    assert not any(hidden in text for hidden in ("dataset", "experiment", "prompt", "issue"))


async def test_a_filter_refusal_lists_the_types_the_mode_filters() -> None:
    with pytest.raises(ToolError) as exc:
        await run_list("project", filters="name = 'x'", settings=SPEND, client=NO_BACKEND)
    text = _refusal(exc)
    assert "Filterable types: trace, span, thread" in text
    assert "experiment" not in text


def test_schema_refuses_write_operations_and_hidden_list_keys_in_cost_intelligence() -> None:
    for key in ("update_thread", "create_dataset", "list.dataset_item", "list.experiment"):
        with pytest.raises(ToolError) as exc:
            run_schema(key, settings=SPEND)
        text = _refusal(exc)
        assert "not available in this workspace" in text
        assert "list.trace" in text
        assert "dataset" not in text.replace(key, "")


def test_schema_still_serves_visible_list_keys_in_cost_intelligence() -> None:
    assert run_schema("list.trace", settings=SPEND)["operation"] == "list.trace"
    assert run_schema("list.project_metric", settings=SPEND)


def test_schema_serves_write_operations_and_every_list_key_in_the_default_mode() -> None:
    for key in (*LIST_SCHEMA_KEYS, WRITE_OPERATIONS[0]):
        assert run_schema(key, settings=DEFAULT)


def test_a_missing_record_offers_the_listing_the_mode_has() -> None:
    from opik_mcp.client.base import OpikNotFoundError
    from opik_mcp.read_list.read_tool import _format_client_error

    for entity_type, mode in (
        ("spend_lane", COST_INTELLIGENCE_MODE),
        ("trace", COST_INTELLIGENCE_MODE),
        ("trace", DEFAULT_MODE),
    ):
        text = _format_client_error(entity_type, "x", OpikNotFoundError("m"), mode)
        assert f"find it with list({entity_type!r}" in text
    default = _format_client_error("spend_lane", "x", OpikNotFoundError("m"), DEFAULT_MODE)
    assert "find it with" not in default
