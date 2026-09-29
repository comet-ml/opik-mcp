"""Cost intelligence mode over a real stdio process, against the stub.

A local server whose workspace starts with ``__ai_spend_`` shows a smaller
surface, is confined to the ``claude-code`` project and serves the spend types.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession

from opik_mcp.cost_intelligence import FIXED_PROJECT
from tests.hermetic.reads.answers import call, refuse
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

MODE_WORKSPACE = "__ai_spend_test__"
SIZED = re.compile(r"^\[(read|list): spend_\w+(?: [^|\]]+)? \| [\d,]+ tok(?: \| .*)?\]$")
SPEND_LISTS = ("spend_summary", "spend_lane", "spend_user", "spend_session", "spend_agent")


@asynccontextmanager
async def mode_session(stub: StubBackend) -> AsyncIterator[ClientSession]:
    stub.project_name = FIXED_PROJECT
    async with stdio_session(stub, OPIK_WORKSPACE=MODE_WORKSPACE) as session:
        yield session


async def test_the_mode_offers_only_the_read_tools(backend: StubBackend) -> None:
    async with mode_session(backend) as session:
        names = [tool.name for tool in (await session.list_tools()).tools]
    assert names == ["read", "list", "schema", "read_skill"]


async def test_every_spend_answer_states_its_size(backend: StubBackend) -> None:
    async with mode_session(backend) as session:
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
    async with mode_session(backend) as session:
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
    async with mode_session(backend) as session:
        refusal = await refuse(session, "list", entity_type="spend_user")
    assert "organization admin" in refusal
    assert "Failed to" not in refusal


async def test_a_hidden_type_and_another_project_are_refused(backend: StubBackend) -> None:
    async with mode_session(backend) as session:
        await refuse(session, "list", entity_type="dataset")
        refusal = await refuse(session, "list", entity_type="trace", project_name="other")
        await call(session, "list", entity_type="trace")
    assert FIXED_PROJECT in refusal
    assert {r.query["project_name"][0] for r in backend.sent("/v1/private/traces")} == {
        FIXED_PROJECT
    }


async def test_the_guide_is_served_and_other_skills_are_not_offered(backend: StubBackend) -> None:
    async with mode_session(backend) as session:
        guide = await call(session, "read_skill", skill_name="cost-intelligence")
        refusal = await refuse(session, "read_skill", skill_name="opik-instrument")
        resources = (await session.list_resources()).resources
    assert "name: cost-intelligence" in guide
    assert "not offered" in refusal
    assert resources == [], "the mode publishes no skill resources, the guide included"


async def test_the_default_workspace_has_no_spend_types_and_no_guide(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        listing = await refuse(session, "list", entity_type="spend_user")
        skill = await refuse(session, "read_skill", skill_name="cost-intelligence")
    assert "spend_user" not in listing.split("Listable types:")[1]
    assert "unknown skill" in skill
    assert not backend.called("/v1/private/ai-spend")
