"""An AI Spend workspace over a real stdio process, against the stub.

A local server whose workspace starts with ``__ai_spend_`` hides nothing: it
offers the default tools and skills, and adds the cost intelligence guide.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession

from opik_mcp.cost_intelligence import FIXED_PROJECT
from opik_mcp.skills_catalog import skill_names
from tests.hermetic.reads.answers import call, refuse
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

SPEND_WORKSPACE = "__ai_spend_test__"
SIZED = re.compile(r"^\[(read|list): spend_\w+(?: [^|\]]+)? \| [\d,]+ tok(?: \| .*)?\]$")
SPEND_LISTS = ("spend_summary", "spend_lane", "spend_user", "spend_session", "spend_agent")


@asynccontextmanager
async def spend_session(stub: StubBackend) -> AsyncIterator[ClientSession]:
    stub.project_name = FIXED_PROJECT
    async with stdio_session(stub, OPIK_WORKSPACE=SPEND_WORKSPACE) as session:
        yield session


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


async def test_every_spend_answer_states_its_size(backend: StubBackend) -> None:
    async with spend_session(backend) as session:
        answers = [await call(session, "list", entity_type=entity) for entity in SPEND_LISTS]
        answers.append(await call(session, "read", entity_type="spend_lane", id="mcp_servers"))
        answers.append(await call(session, "read", entity_type="spend_session", id="session-aaa"))
    for answer in answers:
        header = answer.split("\n", 1)[0]
        assert SIZED.fullmatch(header), f"header states no size: {header}"
    for route in ("summary", "composition", "users", "sessions", "agents"):
        (sent,) = [r for r in backend.requests if r.path == f"/v1/private/ai-spend/{route}"]
        body = sent.payload
        assert body["project_name"] == FIXED_PROJECT
        assert {"interval_start", "interval_end"} <= body.keys()


async def test_sessions_are_narrowed_without_a_user_email_in_the_body(
    backend: StubBackend,
) -> None:
    async with spend_session(backend) as session:
        await call(
            session,
            "list",
            entity_type="spend_session",
            filters='user_email = "dev@example.com"',
        )
    request = backend.one("/v1/private/ai-spend/sessions")
    assert "user_email" not in request.payload
    assert "dev@example.com" in request.query["filters"][0]


async def test_a_403_reads_as_the_admin_sentence(backend: StubBackend) -> None:
    backend.forbidding = {"/v1/private/ai-spend"}
    async with spend_session(backend) as session:
        refusal = await refuse(session, "list", entity_type="spend_user")
    assert "organization admin" in refusal
    assert "Failed to" not in refusal


async def test_the_default_workspace_has_no_spend_types(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        listing = await refuse(session, "list", entity_type="spend_user")
    assert "spend_user" not in listing.split("Listable types:")[1]
    assert not backend.called("/v1/private/ai-spend")
