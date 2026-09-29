"""Which servers turn the AI Spend feature on."""

from __future__ import annotations

import pytest

from opik_mcp.cost_intelligence import (
    AI_SPEND_FEATURE,
    WORKSPACE_PREFIX,
    enabled_features,
    is_cost_intelligence,
)
from opik_mcp.cost_intelligence.descriptions import GUIDE_NAME
from opik_mcp.skills_catalog import UnknownSkillError, run_read_skill
from tests.factories import make_settings

SPEND_WORKSPACE = f"{WORKSPACE_PREFIX}org123__"


def test_a_local_server_on_a_spend_workspace_turns_the_feature_on() -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="stdio")
    assert is_cost_intelligence(settings)
    assert enabled_features(settings) == frozenset({AI_SPEND_FEATURE})


def test_the_workspace_may_come_from_the_older_variable_name() -> None:
    assert is_cost_intelligence(make_settings(comet_workspace=SPEND_WORKSPACE))


def test_the_hosted_transport_never_turns_the_feature_on() -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="streamable-http")
    assert not is_cost_intelligence(settings)
    assert enabled_features(settings) == frozenset()


@pytest.mark.parametrize("workspace", [None, "default", "my-team", "ai_spend_org", "x__ai_spend_y"])
def test_any_other_workspace_has_no_features(workspace: str | None) -> None:
    assert enabled_features(make_settings(opik_workspace=workspace)) == frozenset()


@pytest.mark.parametrize("spelling", ["STDIO", "Stdio"])
def test_the_transport_spelling_does_not_change_the_features(spelling: str) -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport=spelling)
    assert enabled_features(settings) == frozenset({AI_SPEND_FEATURE})


def test_the_guide_is_served_only_when_the_feature_is_on() -> None:
    served = run_read_skill(GUIDE_NAME, frozenset({AI_SPEND_FEATURE}))
    assert served.startswith(f"[read_skill: {GUIDE_NAME} bytes=")
    with pytest.raises(UnknownSkillError):
        run_read_skill(GUIDE_NAME)
