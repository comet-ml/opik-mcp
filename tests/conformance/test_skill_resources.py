"""Conformance — skills served as cacheable MCP resources (OPIK-7472).

Asserts over a real MCP session, not against the module's internals, because the
thing being promised is a wire contract: a connected agent can list the skills,
read any one of them, and is told it may cache the result.

The cache metadata deserves its own note. `mcp` 1.27 does not declare the
2026-07-28 spec's `CacheableResult` fields, so `ttlMs` / `cacheScope` ride as
pydantic model extras — which works only because every MCP result model is
`extra="allow"`. That is an SDK implementation detail we depend on, so it is
pinned here: an upgrade that tightens `extra` fails these tests instead of
silently dropping the cache metadata and quietly costing every host a refetch
per session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextResourceContents
from pydantic import AnyUrl

from opik_mcp import skills_catalog as catalog
from opik_mcp.server import mcp
from opik_mcp.skills_resources import install_skill_resources

SKILLS_SRC = Path(__file__).resolve().parents[2] / "src" / "opik_mcp" / "skills"

# The handlers are installed by `build_app()` / `__main__` in production; neither
# runs under test, so install them here against the shared server instance. The
# swap is by assignment into `request_handlers`, so repeated installs across test
# modules replace rather than stack.
install_skill_resources(mcp)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _cache_fields(result: Any) -> dict[str, Any]:
    """The cache metadata as it arrived at the client, from wherever it landed:
    a declared field once the SDK adds one, model extras until then."""
    extras = dict(result.model_extra or {})
    return {
        "ttlMs": getattr(result, "ttlMs", extras.get("ttlMs")),
        "cacheScope": getattr(result, "cacheScope", extras.get("cacheScope")),
    }


def _assert_cacheable(result: Any, where: str) -> None:
    fields = _cache_fields(result)
    assert fields["ttlMs"] == catalog.SKILLS_TTL_MS, f"{where}: ttlMs missing or wrong ({fields})"
    assert fields["cacheScope"] == catalog.SKILLS_CACHE_SCOPE, (
        f"{where}: cacheScope missing or wrong ({fields})"
    )


@pytest.mark.anyio
async def test_an_agent_can_list_every_bundled_skill_file() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        listed = await session.list_resources()

    advertised = {str(r.uri) for r in listed.resources}
    expected = {f.uri for f in catalog.iter_skill_files()}
    assert expected <= advertised, f"missing from resources/list: {sorted(expected - advertised)}"
    # Every skill's entry point is reachable — the document an agent reads first.
    for name in catalog.skill_names():
        assert f"{catalog.SKILLS_URI_PREFIX}{name}/SKILL.md" in advertised


@pytest.mark.anyio
async def test_resources_list_carries_cache_metadata() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        listed = await session.list_resources()
    _assert_cacheable(listed, "resources/list")


@pytest.mark.anyio
async def test_resources_list_is_deterministic_across_sessions() -> None:
    """The spec expects a deterministic listing and hosts cache it. Two sessions
    must agree on order, not merely on membership."""
    orders = []
    for _ in range(2):
        async with create_connected_server_and_client_session(mcp._mcp_server) as session:
            await session.initialize()
            listed = await session.list_resources()
        orders.append([str(r.uri) for r in listed.resources])
    assert orders[0] == orders[1]


@pytest.mark.anyio
async def test_every_advertised_skill_resource_reads_back_verbatim() -> None:
    """The acceptance criterion in full: served content matches the skills bundled
    in `opik-mcp`, for every file advertised — not just a sampled one."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        for entry in catalog.iter_skill_files():
            result = await session.read_resource(AnyUrl(entry.uri))
            assert len(result.contents) == 1, entry.uri
            content = result.contents[0]
            assert isinstance(content, TextResourceContents), entry.uri
            on_disk = (SKILLS_SRC / entry.skill / entry.path).read_text(encoding="utf-8")
            assert content.text == on_disk, f"{entry.uri} does not match the packaged file"
            assert content.mimeType == entry.mime_type


@pytest.mark.anyio
async def test_resources_read_carries_cache_metadata_on_the_result_and_the_content() -> None:
    """Both places: hosts differ on which one they read, and the metadata is two
    fields, so serving it twice is cheaper than serving it to the wrong half."""
    uri = AnyUrl(f"{catalog.SKILLS_URI_PREFIX}opik/SKILL.md")
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.read_resource(uri)
    _assert_cacheable(result, "resources/read")
    assert result.contents[0].meta == {
        "ttlMs": catalog.SKILLS_TTL_MS,
        "cacheScope": catalog.SKILLS_CACHE_SCOPE,
    }, "per-content _meta missing — check the `_meta` alias, not `meta=`"


@pytest.mark.anyio
async def test_a_resource_template_advertises_the_uri_shape() -> None:
    """So a host can construct a URI without holding the whole listing."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        templates = await session.list_resource_templates()
    shapes = {t.uriTemplate for t in templates.resourceTemplates}
    assert f"{catalog.SKILLS_URI_PREFIX}{{skill}}/{{path}}" in shapes
    _assert_cacheable(templates, "resources/templates/list")


@pytest.mark.anyio
async def test_reading_an_unknown_resource_is_an_error_not_an_empty_success() -> None:
    """An empty success is the dangerous answer: the host caches "this skill is
    blank" for as long as the TTL we just handed it."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        with pytest.raises(McpError):
            await session.read_resource(AnyUrl(f"{catalog.SKILLS_URI_PREFIX}nope/SKILL.md"))


@pytest.mark.anyio
async def test_excluded_directories_are_not_reachable_over_the_wire() -> None:
    """`evals/` is excluded from the published pack; it must be equally absent
    here, both from the listing and from a direct read of a known fixture path."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        listed = await session.list_resources()
        with pytest.raises(McpError):
            await session.read_resource(
                AnyUrl(f"{catalog.SKILLS_URI_PREFIX}opik-diagnose/evals/metrics.py")
            )
    assert not [r for r in listed.resources if "/evals/" in str(r.uri)]
