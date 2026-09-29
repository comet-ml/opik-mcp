"""Which servers enter cost intelligence mode."""

from __future__ import annotations

import pytest

from opik_mcp.cost_intelligence import (
    COST_INTELLIGENCE_MODE,
    DEFAULT_MODE,
    WORKSPACE_PREFIX,
    is_cost_intelligence,
    mode_of,
)
from tests.factories import make_settings

SPEND_WORKSPACE = f"{WORKSPACE_PREFIX}org123__"


def test_a_local_server_on_a_spend_workspace_enters_the_mode() -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="stdio")
    assert is_cost_intelligence(settings)
    assert mode_of(settings) == COST_INTELLIGENCE_MODE


def test_the_workspace_may_come_from_the_older_variable_name() -> None:
    assert is_cost_intelligence(make_settings(comet_workspace=SPEND_WORKSPACE))


def test_the_hosted_transport_never_enters_the_mode() -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="streamable-http")
    assert not is_cost_intelligence(settings)
    assert mode_of(settings) == DEFAULT_MODE


@pytest.mark.parametrize("workspace", [None, "default", "my-team", "ai_spend_org", "x__ai_spend_y"])
def test_any_other_workspace_stays_in_the_default_mode(workspace: str | None) -> None:
    assert mode_of(make_settings(opik_workspace=workspace)) == DEFAULT_MODE


def test_read_skill_and_schema_use_the_registration_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import anyio

    from opik_mcp.cost_intelligence.descriptions import GUIDE_NAME
    from opik_mcp.server.tools import read_skill as skill_tool
    from opik_mcp.server.tools import schema as schema_tool

    def refuse() -> None:
        raise AssertionError("asked the settings at call time")

    monkeypatch.setattr(skill_tool, "get_settings", refuse)
    monkeypatch.setattr("opik_mcp.writes.schema_tool.get_settings", refuse)
    assert anyio.run(schema_tool._build(COST_INTELLIGENCE_MODE), "list.trace")
    assert anyio.run(skill_tool._build(COST_INTELLIGENCE_MODE), GUIDE_NAME)


@pytest.mark.parametrize("spelling", ["STDIO", "Stdio"])
def test_the_transport_spelling_does_not_change_the_mode(spelling: str) -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport=spelling)
    assert mode_of(settings) == COST_INTELLIGENCE_MODE
