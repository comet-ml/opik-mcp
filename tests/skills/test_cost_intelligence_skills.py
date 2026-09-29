"""Which skills a cost intelligence server offers, and where its guide lives."""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from opik_mcp import skills_catalog as catalog
from opik_mcp.cost_intelligence import COST_INTELLIGENCE_MODE, DEFAULT_MODE
from opik_mcp.cost_intelligence.descriptions import GUIDE_NAME
from opik_mcp.server import mcp
from opik_mcp.skills_resources import install_skill_resources
from tests.cost_intelligence.build import build_cost_intelligence_server

MODE = COST_INTELLIGENCE_MODE


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_the_mode_offers_the_guide_and_opik_only() -> None:
    assert catalog.visible_skill_names(MODE) == ("cost-intelligence", "opik")
    assert catalog.visible_skill_names(DEFAULT_MODE) == catalog.skill_names()


@pytest.mark.parametrize("requested", ["cost-intelligence", "cost-intelligence.md"])
def test_the_guide_is_served_in_the_mode(requested: str) -> None:
    out = catalog.run_read_skill(requested, MODE)
    assert out.startswith("[read_skill: cost-intelligence")
    assert "# Cost intelligence" in out


def test_a_bundled_skill_the_mode_hides_is_refused_with_what_is_offered() -> None:
    with pytest.raises(catalog.UnknownSkillError) as raised:
        catalog.run_read_skill("opik-diagnose", MODE)
    assert str(raised.value) == (
        "'opik-diagnose' is not offered in this workspace. Available: cost-intelligence, opik."
    )


def test_a_hidden_skill_is_refused_in_path_form_too() -> None:
    with pytest.raises(catalog.UnknownSkillError, match="not offered in this workspace"):
        catalog.run_read_skill("opik-instrument/references/anything.md", MODE)


def test_an_unknown_name_lists_the_modes_skills() -> None:
    with pytest.raises(catalog.UnknownSkillError, match=r"Available|available skills: cost"):
        catalog.run_read_skill("nope", MODE)


def test_an_offered_bundled_skill_is_still_served() -> None:
    assert catalog.run_read_skill("opik", MODE) == catalog.run_read_skill("opik")


def test_the_guide_is_an_unknown_skill_in_the_default_mode() -> None:
    with pytest.raises(catalog.UnknownSkillError) as guide:
        catalog.run_read_skill("cost-intelligence")
    with pytest.raises(catalog.UnknownSkillError) as other:
        catalog.run_read_skill("nope")
    assert str(guide.value).replace("'cost-intelligence'", "X") == str(other.value).replace(
        "'nope'", "X"
    )


def test_the_guide_is_not_part_of_the_bundled_skills_or_their_uris() -> None:
    assert GUIDE_NAME not in catalog.skill_names()
    assert not any(GUIDE_NAME in f.uri for f in catalog.iter_skill_files())
    assert catalog.resolve_uri(f"{catalog.SKILLS_URI_PREFIX}{GUIDE_NAME}/SKILL.md") is None


def test_the_modes_tool_description_names_no_hidden_skill() -> None:
    text = catalog.read_skill_tool_description(MODE)
    assert GUIDE_NAME in text
    assert "opik-instrument" not in text


def test_the_default_tool_description_is_unchanged_by_the_mode_argument() -> None:
    assert catalog.read_skill_tool_description() == catalog.read_skill_tool_description(
        DEFAULT_MODE
    )
    assert GUIDE_NAME not in catalog.read_skill_tool_description()


@pytest.mark.anyio
async def test_the_read_skill_tool_serves_the_guide_and_refuses_a_hidden_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = build_cost_intelligence_server(monkeypatch)
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        guide = await client.call_tool("read_skill", {"skill_name": GUIDE_NAME})
        hidden = await client.call_tool("read_skill", {"skill_name": "opik-verify"})
    assert not guide.isError
    assert isinstance(guide.content[0], TextContent)
    assert "# Cost intelligence" in guide.content[0].text
    assert hidden.isError
    assert isinstance(hidden.content[0], TextContent)
    assert "not offered in this workspace" in hidden.content[0].text


@pytest.mark.anyio
async def test_the_guide_never_appears_among_the_resources() -> None:
    install_skill_resources(mcp)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        uris = [str(r.uri) for r in (await client.list_resources()).resources]
    assert uris
    assert not any(GUIDE_NAME in uri for uri in uris)


def test_the_stdio_transport_in_the_mode_installs_no_skill_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import opik_mcp.analytics.wrappers as wrappers
    import opik_mcp.server as server_package
    import opik_mcp.skills_resources as resources
    from opik_mcp.__main__ import _run_transport
    from tests.factories import make_settings

    installed: list[object] = []
    ran: list[str] = []

    class _Server:
        def run(self, transport: str) -> None:
            ran.append(transport)

    monkeypatch.setattr(server_package, "mcp", _Server())
    monkeypatch.setattr(wrappers, "install_tools_listed_emitter", lambda _server: None)
    monkeypatch.setattr(resources, "install_skill_resources", installed.append)

    _run_transport(make_settings(comet_workspace="__ai_spend_test__"), "stdio")
    assert ran == ["stdio"]
    assert installed == [], "cost intelligence mode must not serve skills as public resources"

    _run_transport(make_settings(comet_workspace="team"), "stdio")
    assert len(installed) == 1, "the default stdio server still serves skill resources"
