"""The cost intelligence guide: an extra skill in an AI Spend workspace, outside the pack."""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from opik_mcp import skills_catalog as catalog
from opik_mcp.cost_intelligence import AI_SPEND_FEATURE
from opik_mcp.cost_intelligence.descriptions import GUIDE_NAME
from opik_mcp.server import mcp
from opik_mcp.skills_resources import install_skill_resources
from tests.cost_intelligence.build import build_cost_intelligence_server

SPEND = frozenset({AI_SPEND_FEATURE})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("requested", ["cost-intelligence", "cost-intelligence.md"])
def test_the_guide_is_served_with_the_feature(requested: str) -> None:
    out = catalog.run_read_skill(requested, SPEND)
    assert out.startswith("[read_skill: cost-intelligence")
    assert "# Cost intelligence" in out


def test_every_bundled_skill_is_still_served_with_the_feature() -> None:
    for name in catalog.skill_names():
        assert catalog.run_read_skill(name, SPEND) == catalog.run_read_skill(name)


def test_the_guide_is_an_unknown_skill_without_the_feature() -> None:
    with pytest.raises(catalog.UnknownSkillError) as guide:
        catalog.run_read_skill(GUIDE_NAME)
    with pytest.raises(catalog.UnknownSkillError) as other:
        catalog.run_read_skill("nope")
    assert str(guide.value).replace("'cost-intelligence'", "X") == str(other.value).replace(
        "'nope'", "X"
    )


def test_an_unknown_skill_names_the_guide_only_with_the_feature() -> None:
    with pytest.raises(catalog.UnknownSkillError) as spend:
        catalog.run_read_skill("nonexistent-skill", SPEND)
    with pytest.raises(catalog.UnknownSkillError) as default:
        catalog.run_read_skill("nonexistent-skill")
    listed = str(spend.value).split("available skills: ")[1].split(", ")
    assert GUIDE_NAME in listed
    assert listed == sorted(listed)
    assert GUIDE_NAME not in str(default.value)
    assert str(default.value) == (
        f"unknown skill 'nonexistent-skill'; available skills: {', '.join(catalog.skill_names())}"
    )


def test_the_guide_is_not_part_of_the_bundled_skills_or_their_uris() -> None:
    assert GUIDE_NAME not in catalog.skill_names()
    assert not any(GUIDE_NAME in f.uri for f in catalog.iter_skill_files())
    assert catalog.resolve_uri(f"{catalog.SKILLS_URI_PREFIX}{GUIDE_NAME}/SKILL.md") is None


def test_the_default_tool_description_does_not_name_the_guide() -> None:
    assert GUIDE_NAME not in catalog.read_skill_tool_description()


@pytest.mark.anyio
async def test_the_spend_server_serves_the_guide_and_every_bundled_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = build_cost_intelligence_server(monkeypatch)
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        await client.initialize()
        guide = await client.call_tool("read_skill", {"skill_name": GUIDE_NAME})
        bundled = await client.call_tool("read_skill", {"skill_name": "opik-verify"})
    assert not bundled.isError
    assert not guide.isError
    assert isinstance(guide.content[0], TextContent)
    assert "# Cost intelligence" in guide.content[0].text


@pytest.mark.anyio
async def test_the_default_server_does_not_offer_the_guide() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        await client.initialize()
        described = {t.name: t.description or "" for t in (await client.list_tools()).tools}
        answer = await client.call_tool("read_skill", {"skill_name": GUIDE_NAME})
    assert GUIDE_NAME not in described["read_skill"]
    assert answer.isError


@pytest.mark.anyio
async def test_the_guide_never_appears_among_the_resources() -> None:
    install_skill_resources(mcp)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        uris = [str(r.uri) for r in (await client.list_resources()).resources]
    assert uris
    assert not any(GUIDE_NAME in uri for uri in uris)
