"""End-to-end: the server as a real subprocess, driven over stdio.

WHAT THIS COVERS THAT NOTHING ELSE DOES. Every other suite builds the FastMCP
instance in-process — `tests/conformance` even speaks real MCP over an in-memory
transport — so all of them exercise `server.mcp` directly and none of them runs
`__main__`. But stdio is how Claude Code and Cursor actually launch this server,
and `__main__._run_transport` has its own startup path: it calls
`apply_tool_visibility`, `install_tools_listed_emitter` and
`install_skill_resources` itself, separately from `build_app()`.

The concrete regression: delete the `install_skill_resources(mcp)` line from
`__main__` and every existing test still passes, while every stdio host silently
loses the resource surface. These tests fail instead.

They are e2e in the sense that matters here — a real process, a real pipe, a real
client session — not in the sense of talking to a live Opik backend: skills ship
in the wheel, so nothing here needs credentials or a network. That keeps the job
hermetic enough to run on every PR.

Marked `e2e` and excluded from the default `pytest` run (see `pyproject.toml`);
`make e2e` and the `e2e` CI job select them.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import TextResourceContents
from pydantic import AnyUrl

from opik_mcp.skills_catalog import (
    SKILLS_CACHE_SCOPE,
    SKILLS_TTL_MS,
    SKILLS_URI_PREFIX,
    iter_skill_files,
    skill_names,
)

#: A hung server must fail the job, not hang the runner until GitHub's own
#: timeout kills it 6 hours later with no useful output.
_TIMEOUT_S = 60

#: Tools every deployment advertises. Deliberately a subset check, not equality:
#: `ask_ollie` is opt-in (`OPIK_MCP_ASK_OLLIE_ENABLED`, default false) and
#: `apply_tool_visibility` removes it, so an exact set would encode this job's env
#: rather than the product. `tests/conformance/test_tool_inventory.py` owns the
#: exact-surface rule; what this file owns is that the stdio path advertises at all.
ALWAYS_ADVERTISED = frozenset({"read", "list", "write", "schema", "run_experiment", "read_skill"})


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        # The interpreter running the tests, so the subprocess uses the same
        # environment the suite was installed into rather than whatever `python`
        # resolves to on the runner.
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            # A CI run is not product usage. `is_ci` would label these events
            # rather than suppress them, and a test suite must not post to the
            # analytics endpoint at all.
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            # Keep the subprocess's own logging off the captured stderr.
            "OPIK_MCP_LOG_LEVEL": "WARNING",
        },
    )


@asynccontextmanager
async def _session() -> AsyncIterator[ClientSession]:
    """A live client session against a freshly spawned server subprocess."""
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(_server_params()) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_server_starts_and_completes_a_handshake() -> None:
    """The floor: `python -m opik_mcp` boots with no configuration and speaks MCP.

    No credentials are set, deliberately — a server that needs them to reach the
    handshake would be unusable for a first-run user whose host launches it before
    they have configured anything.
    """
    async with _session() as session:
        result = await session.send_ping()
    assert result is not None


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_stdio_path_advertises_the_tool_surface() -> None:
    async with _session() as session:
        tools = await session.list_tools()
    advertised = {t.name for t in tools.tools}
    missing = ALWAYS_ADVERTISED - advertised
    assert not missing, f"stdio startup did not advertise: {sorted(missing)}"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_stdio_path_installs_the_skill_resources() -> None:
    """The regression this whole file exists for.

    `__main__` installs the resource handlers itself; `build_app()` — which every
    other test reaches — installs them on its own path. Losing the `__main__` call
    is invisible to the rest of the suite and breaks every stdio host.
    """
    async with _session() as session:
        listed = await session.list_resources()

    advertised = {str(r.uri) for r in listed.resources}
    expected = {f.uri for f in iter_skill_files()}
    assert expected <= advertised, f"missing over stdio: {sorted(expected - advertised)}"

    extras = listed.model_extra or {}
    assert extras.get("ttlMs") == SKILLS_TTL_MS
    assert extras.get("cacheScope") == SKILLS_CACHE_SCOPE


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_skill_reads_back_over_the_wire() -> None:
    """Content survives the pipe intact — the transport frames it in chunks, and a
    truncation here would hand an agent a silently half-read skill."""
    uri = f"{SKILLS_URI_PREFIX}opik/SKILL.md"
    async with _session() as session:
        result = await session.read_resource(AnyUrl(uri))

    content = result.contents[0]
    assert isinstance(content, TextResourceContents)
    assert content.text.lstrip().startswith("---"), "frontmatter missing — content truncated?"
    assert "name: opik" in content.text


@pytest.mark.e2e
@pytest.mark.anyio
async def test_read_skill_returns_a_real_document_over_stdio() -> None:
    async with _session() as session:
        result = await session.call_tool("read_skill", {"skill_name": "opik-instrument"})

    assert not result.isError, result.content
    text = getattr(result.content[0], "text", "")
    assert text.startswith("[read_skill: opik-instrument path=SKILL.md")
    assert "name: opik-instrument" in text


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_bad_argument_comes_back_as_a_tool_error_not_a_crash() -> None:
    """A rejected argument must fail the call, not the session. If the server died
    here, the host would drop every later call in the conversation too."""
    async with _session() as session:
        result = await session.call_tool("read_skill", {"skill_name": "not-a-skill"})
        assert result.isError
        assert "available skills" in getattr(result.content[0], "text", "")

        # The session is still usable — that is the half worth asserting.
        after = await session.call_tool("read_skill", {"skill_name": "opik"})
    assert not after.isError


@pytest.mark.e2e
@pytest.mark.anyio
async def test_an_unknown_skill_uri_is_an_error_not_an_empty_success() -> None:
    """An empty success is the dangerous answer: the host caches "this skill is
    blank" for as long as the TTL the listing just handed it."""
    async with _session() as session:
        with pytest.raises(McpError):
            await session.read_resource(AnyUrl(f"{SKILLS_URI_PREFIX}nope/SKILL.md"))


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_session_instructions_name_the_skills() -> None:
    """`install_session_instructions` is wired on the `build_app()` path; on stdio
    the blob comes from FastMCP's constructor argument. Both must carry the
    skill guidance, or the tool ships unmentioned to exactly the hosts that read
    instructions."""
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(_server_params()) as (read, write),
            ClientSession(read, write) as session,
        ):
            result = await session.initialize()

    instructions = result.instructions or ""
    assert "read_skill" in instructions
    for name in skill_names():
        assert name in instructions
