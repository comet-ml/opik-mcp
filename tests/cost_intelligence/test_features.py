"""Which servers turn the AI Spend feature on: ``Settings.features``."""

from __future__ import annotations

import pytest

from opik_mcp.config import AI_SPEND_FEATURE, AI_SPEND_WORKSPACE_PREFIX
from opik_mcp.cost_intelligence.feature import GUIDE_NAME
from opik_mcp.skills_catalog import UnknownSkillError, run_read_skill
from tests.factories import make_settings

SPEND_WORKSPACE = f"{AI_SPEND_WORKSPACE_PREFIX}org123__"
ON = frozenset({AI_SPEND_FEATURE})


def test_a_local_server_on_a_spend_workspace_turns_the_feature_on() -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="stdio")
    assert settings.features == ON


def test_the_workspace_may_come_from_the_older_variable_name() -> None:
    assert make_settings(comet_workspace=SPEND_WORKSPACE).features == ON


def test_the_hosted_transport_never_turns_the_feature_on() -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="streamable-http")
    assert settings.features == frozenset()


@pytest.mark.parametrize("workspace", [None, "default", "my-team", "ai_spend_org", "x__ai_spend_y"])
def test_any_other_workspace_has_no_features(workspace: str | None) -> None:
    assert make_settings(opik_workspace=workspace).features == frozenset()


@pytest.mark.parametrize("spelling", ["STDIO", "Stdio"])
def test_the_transport_spelling_does_not_change_the_features(spelling: str) -> None:
    settings = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport=spelling)
    assert settings.features == ON


def test_no_environment_variable_turns_a_feature_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPIK_FEATURES", "FEATURES", "_FEATURES", "OPIK_MCP_FEATURES"):
        monkeypatch.setenv(name, AI_SPEND_FEATURE)
    assert make_settings(opik_workspace="my-team").features == frozenset()


def test_the_guide_is_served_only_when_the_feature_is_on() -> None:
    on = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="stdio")
    off = make_settings(opik_workspace="my-team")
    served = run_read_skill(GUIDE_NAME, on)
    assert served.startswith(f"[read_skill: {GUIDE_NAME} bytes=")
    with pytest.raises(UnknownSkillError):
        run_read_skill(GUIDE_NAME, off)


@pytest.mark.parametrize(
    "spelling",
    [GUIDE_NAME, f"{GUIDE_NAME}/SKILL.md", f"opik://skills/{GUIDE_NAME}/SKILL.md"],
)
def test_the_guide_answers_to_every_form_a_bundled_skill_accepts(spelling: str) -> None:
    on = make_settings(opik_workspace=SPEND_WORKSPACE, opik_mcp_transport="stdio")
    assert run_read_skill(spelling, on).startswith(f"[read_skill: {GUIDE_NAME} bytes=")
