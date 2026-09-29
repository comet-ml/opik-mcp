"""An AI Spend workspace over a real stdio process, against the stub.

A local server whose workspace starts with ``__ai_spend_`` hides nothing: it
offers the default tools and skills, and adds the cost intelligence guide.
"""

from __future__ import annotations

import pytest

from opik_mcp.skills_catalog import skill_names
from tests.hermetic.reads.answers import call, refuse
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

SPEND_WORKSPACE = "__ai_spend_test__"


async def test_the_workspace_still_offers_every_default_tool(backend: StubBackend) -> None:
    async with stdio_session(backend, OPIK_WORKSPACE=SPEND_WORKSPACE) as session:
        names = [tool.name for tool in (await session.list_tools()).tools]
    assert names == ["read", "list", "write", "schema", "read_skill"]


async def test_the_guide_is_served_beside_every_bundled_skill(backend: StubBackend) -> None:
    async with stdio_session(backend, OPIK_WORKSPACE=SPEND_WORKSPACE) as session:
        guide = await call(session, "read_skill", skill_name="cost-intelligence")
        bundled = [await call(session, "read_skill", skill_name=name) for name in skill_names()]
    assert "name: cost-intelligence" in guide
    assert all(text.startswith("[read_skill: ") for text in bundled)


async def test_the_resources_stay_installed_and_never_carry_the_guide(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend, OPIK_WORKSPACE=SPEND_WORKSPACE) as session:
        uris = [str(r.uri) for r in (await session.list_resources()).resources]
    assert uris
    assert not any("cost-intelligence" in uri for uri in uris)


async def test_the_default_workspace_has_no_guide(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        skill = await refuse(session, "read_skill", skill_name="cost-intelligence")
    assert "unknown skill" in skill
