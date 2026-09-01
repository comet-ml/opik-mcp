"""End-to-end: the server as a real subprocess, driven over stdio.

WHAT THIS COVERS THAT NOTHING ELSE DOES. Every other suite builds the FastMCP
instance in-process — `tests/conformance` even speaks real MCP over an in-memory
transport — so all of them exercise `server.mcp` directly and none of them runs
`__main__`. But stdio is how Claude Code and Cursor actually launch this server,
and `__main__._run_transport` has its own startup path: it calls
`apply_tool_visibility`, `install_tools_listed_emitter` and
`install_skill_resources` itself, separately from `build_app()`.

Two concrete regressions this file exists for, both of which shipped or nearly
shipped because no in-process test could see them:

1. Delete the `install_skill_resources(mcp)` line from `__main__` and every
   other test still passes, while every stdio host silently loses the resource
   surface.
2. The session instructions described `ask_ollie` while `apply_tool_visibility`
   was removing it from the registry — two mechanisms that must agree, with
   nothing structural keeping them in step. Reported from a live session twice;
   the unit tests could not catch it because they call the renderer directly
   instead of asking a running server what it sends.

They are e2e in the sense that matters here — a real process, a real pipe, a real
client session — not in the sense of talking to a live Opik backend: skills ship
in the wheel, so nothing here needs credentials or a network. That keeps the job
hermetic enough to run on every PR.

Marked `e2e` and excluded from the default `pytest` run (see `pyproject.toml`);
`make e2e` and the `e2e` CI job select them.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import InitializeResult, TextResourceContents, Tool
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


def _server_params(**extra_env: str) -> StdioServerParameters:
    return StdioServerParameters(
        # The interpreter running the tests, so the subprocess uses the same
        # environment the suite was installed into rather than whatever `python`
        # resolves to on the runner.
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            # Both telemetry channels off, explicitly rather than by inheritance.
            # A CI run is not product usage: `is_ci` would label these events
            # rather than suppress them, and a test must not post to the
            # analytics endpoint or the Sentry project at all. conftest already
            # puts both flags in os.environ, and `setup_sentry` has its own
            # pytest guard — but this server runs in a child process, so the
            # opt-out is restated where it takes effect instead of resting on
            # two layers of inheritance.
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "OPIK_MCP_SENTRY_ENABLED": "false",
            # Keep the subprocess's own logging off the captured stderr.
            "OPIK_MCP_LOG_LEVEL": "WARNING",
            # Per-test overrides last, so a test can flip a feature flag and get
            # a server configured the way a real deployment would be.
            **extra_env,
        },
    )


@asynccontextmanager
async def _session(**extra_env: str) -> AsyncIterator[ClientSession]:
    """A live client session against a freshly spawned server subprocess."""
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(_server_params(**extra_env)) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


@asynccontextmanager
async def _handshake(**extra_env: str) -> AsyncIterator[tuple[InitializeResult, list[Tool]]]:
    """The handshake result and the advertised tools, from one live server.

    Both halves from the SAME process: the point of the tests using this is that
    two independent mechanisms agree, and reading them from separate servers
    would let a mismatch pass.
    """
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(_server_params(**extra_env)) as (read, write),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            yield init, tools


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


# --- the blob and the tool list must agree ------------------------------- #
#
# Two independent mechanisms decide what a session is told about:
# `apply_tool_visibility` removes a disabled tool from the registry, and
# `instructions.render_instructions` writes the prose. Nothing structural keeps
# them in step, and they drifted: `ask_ollie` is opt-in and default-OFF, yet the
# blob described it in every session, telling agents to use a tool that was not
# there. It was reported from a live session twice before being fixed, because
# the unit tests call `render_instructions` directly — they never ask a running
# server what it actually sends.
#
# These do. Both halves come from one process, over a real handshake, so a
# mismatch cannot hide behind a second server.


def _bulleted_tool_names(instructions: str) -> set[str]:
    """Tool names the blob presents as `- <name>: …` bullets.

    "Direct writes" is prose introducing `write`/`schema` rather than a tool
    name, so it is excluded by matching only lowercase identifiers.
    """
    return set(re.findall(r"^- ([a-z_]+):", instructions, re.MULTILINE))


@pytest.mark.e2e
@pytest.mark.anyio
async def test_the_blob_describes_no_tool_the_server_does_not_advertise() -> None:
    """The general invariant, against a default-configured server."""
    async with _handshake() as (init, tools):
        advertised = {t.name for t in tools}
        described = _bulleted_tool_names(init.instructions or "")

    phantom = described - advertised
    assert not phantom, (
        f"the session instructions describe {sorted(phantom)}, which this server "
        f"does not advertise (tools/list: {sorted(advertised)})"
    )


@pytest.mark.e2e
@pytest.mark.anyio
async def test_a_disabled_tool_is_absent_from_both_surfaces() -> None:
    """`ask_ollie` off — the default, so this is the common case, not an edge."""
    async with _handshake(OPIK_MCP_ASK_OLLIE_ENABLED="false") as (init, tools):
        assert "ask_ollie" not in {t.name for t in tools}
        assert "ollie" not in (init.instructions or "").lower(), (
            "ask_ollie is not advertised, but the session instructions still "
            "describe it — an agent would spend a turn discovering it is absent"
        )


@pytest.mark.e2e
@pytest.mark.anyio
async def test_an_enabled_tool_is_present_on_both_surfaces() -> None:
    """The other direction, which is what stops the gate being satisfied by
    deleting the text outright: with the flag ON, `ask_ollie` must be advertised
    AND described. Without this, "no Ollie anywhere" would pass forever while
    silently leaving an advertised tool undocumented."""
    async with _handshake(OPIK_MCP_ASK_OLLIE_ENABLED="true") as (init, tools):
        assert "ask_ollie" in {t.name for t in tools}
        instructions = init.instructions or ""
        assert "ask_ollie" in _bulleted_tool_names(instructions), (
            "ask_ollie is advertised but the session instructions do not describe it"
        )


@pytest.mark.e2e
@pytest.mark.anyio
async def test_read_skill_is_described_because_it_is_always_advertised() -> None:
    """The same invariant read the other way for the tool this branch adds: it
    is not behind a flag, so it must appear in both surfaces unconditionally."""
    async with _handshake() as (init, tools):
        assert "read_skill" in {t.name for t in tools}
        assert "read_skill" in _bulleted_tool_names(init.instructions or "")
