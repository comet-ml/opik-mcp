"""Serve the bundled skills as MCP resources (OPIK-7472).

The 2026-07-28 MCP specification has no "skill" primitive, but it adds a
`CacheableResult` contract (`ttlMs`, `cacheScope`) on `resources/list` and
`resources/read` plus a deterministic-listing expectation. Skill content is
static and identical for every caller, so resources are its spec-native home:
a host can fetch the tree once and cache it publicly.

Two implementation facts drive the shape of this module.

*The handlers are installed, not decorated.* `mcp` 1.27's low-level
`@server.read_resource()` decorator builds the `ReadResourceResult` itself, so
there is no seam to attach top-level cache fields to. We swap the registered
handler in place instead — the same in-place swap
`analytics.wrappers.install_tools_listed_emitter` and
`server.install_session_instructions` use.

*The cache fields ride as model extras.* `mcp` 1.27 does not yet declare
`ttlMs` / `cacheScope` on the result types, but every MCP result model is
`extra="allow"`, so the fields serialise and reach the client unchanged.
`tests/conformance/test_skill_resources.py` asserts they arrive over a real
session, which is what turns "extras happen to work" into a pinned contract:
an SDK upgrade that tightened `extra` would fail there instead of silently
dropping the cache metadata on the floor.

Non-skill URIs and any resources registered through FastMCP's own decorator are
delegated to the original handlers, so this composes rather than replaces.
"""

from __future__ import annotations

import logging
from typing import Any

import mcp.types as types
from pydantic import AnyUrl

from opik_mcp.skills_catalog import (
    SKILLS_CACHE_SCOPE,
    SKILLS_TTL_MS,
    SKILLS_URI_PREFIX,
    SkillFile,
    iter_skill_files,
    read_skill_file,
    resolve_uri,
    unknown_skill_uri_message,
)

logger = logging.getLogger("opik_mcp")

#: Advertised so a host can discover the URI shape without listing every file.
_URI_TEMPLATE = f"{SKILLS_URI_PREFIX}{{skill}}/{{path}}"

#: Marker set on the handlers this module installs. Because each handler
#: *delegates* to the one it replaced, a second install would chain onto the
#: first and list every skill twice — so the second install is a no-op instead.
#: More than one call is normal: `build_app()` can run per worker under
#: `--factory`, and tests share one server instance across modules.
_INSTALLED_MARKER = "_opik_skill_resources_installed"

#: Cache metadata, spelled as the wire field names. Applied to both verbs: a host
#: that caches the listing but re-reads every file on every session would still
#: pay for the content it was told it may keep.
_CACHE_FIELDS: dict[str, Any] = {"ttlMs": SKILLS_TTL_MS, "cacheScope": SKILLS_CACHE_SCOPE}

#: The same metadata as a `_meta` bag, spread into constructors as
#: `**_META_KWARGS`. It has to be the alias, not `meta=`: these models don't set
#: `populate_by_name`, so `meta=` is accepted as an *extra* and serialises as a
#: non-spec `"meta"` key while the real `_meta` field stays empty.
_META_KWARGS: dict[str, Any] = {"_meta": dict(_CACHE_FIELDS)}


def _resource(entry: SkillFile) -> types.Resource:
    """One `resources/list` entry.

    `name` is the skill-relative path so a host's resource picker groups a
    skill's files together; `title` is what a human reads in that picker.
    """
    role = "skill entry point" if entry.is_entry_point else "reference"
    return types.Resource(
        uri=AnyUrl(entry.uri),
        name=f"{entry.skill}/{entry.path}",
        title=f"{entry.skill} — {entry.path}",
        description=f"Opik agent skill {entry.skill!r}, {role} ({entry.path}).",
        mimeType=entry.mime_type,
        **_META_KWARGS,
    )


def install_skill_resources(mcp: Any) -> None:
    """Add the skill resources to a FastMCP instance's `resources/*` handlers.

    Idempotent: safe to call from every startup path (see `server.build_app` and
    `__main__`) and from more than one test module. Non-fatal — a server that
    cannot serve skills must still serve tools, so every failure path here logs
    and leaves the original handler in place.
    """
    try:
        lowlevel = mcp._mcp_server
    except AttributeError:
        logger.debug("install_skill_resources: mcp has no _mcp_server attribute")
        return

    original_list = lowlevel.request_handlers.get(types.ListResourcesRequest)
    if getattr(original_list, _INSTALLED_MARKER, False):
        return

    original_read = lowlevel.request_handlers.get(types.ReadResourceRequest)
    original_templates = lowlevel.request_handlers.get(types.ListResourceTemplatesRequest)

    async def list_resources(req: types.ListResourcesRequest) -> types.ServerResult:
        # FastMCP's own resources first (none today — kept so a future
        # `@mcp.resource()` is not silently shadowed by this handler), then ours
        # in the catalog's deterministic order.
        existing: list[types.Resource] = []
        cursor: str | None = None
        if original_list is not None:
            try:
                inner = await original_list(req)
                if isinstance(inner.root, types.ListResourcesResult):
                    existing = list(inner.root.resources)
                    cursor = inner.root.nextCursor
            except Exception:
                logger.debug("skill resources: delegate resources/list failed", exc_info=True)
        return types.ServerResult(
            types.ListResourcesResult(
                resources=[*existing, *(_resource(e) for e in iter_skill_files())],
                nextCursor=cursor,
                **_CACHE_FIELDS,
            )
        )

    async def read_resource(req: types.ReadResourceRequest) -> types.ServerResult:
        uri = str(req.params.uri)
        entry = resolve_uri(uri)
        if entry is None:
            if uri.startswith(SKILLS_URI_PREFIX):
                # Ours by namespace but not a document we have — a typo, or a URI
                # cached from an older build. Delegating would hand back FastMCP's
                # bare "Unknown resource", which tells the caller nothing about what
                # it should have asked for; name the skill's own documents instead,
                # exactly as the tool does for the same mistake.
                raise ValueError(unknown_skill_uri_message(uri))
            if original_read is not None:
                delegated: types.ServerResult = await original_read(req)
                return delegated
            # Not ours and no delegate: the spec's answer for an unknown resource,
            # rather than an empty success the host would cache for a day.
            raise ValueError(f"Unknown resource: {uri}")
        return types.ServerResult(
            types.ReadResourceResult(
                contents=[
                    types.TextResourceContents(
                        uri=req.params.uri,
                        text=read_skill_file(entry),
                        mimeType=entry.mime_type,
                        # Mirrored into per-content `_meta` as well as the
                        # top-level result: hosts differ on which one they read,
                        # and the metadata is two fields.
                        **_META_KWARGS,
                    )
                ],
                **_CACHE_FIELDS,
            )
        )

    async def list_templates(req: types.ListResourceTemplatesRequest) -> types.ServerResult:
        existing: list[types.ResourceTemplate] = []
        if original_templates is not None:
            try:
                inner = await original_templates(req)
                if isinstance(inner.root, types.ListResourceTemplatesResult):
                    existing = list(inner.root.resourceTemplates)
            except Exception:
                logger.debug("skill resources: delegate templates/list failed", exc_info=True)
        template = types.ResourceTemplate(
            uriTemplate=_URI_TEMPLATE,
            name="opik-skill-file",
            title="Opik agent skill file",
            description=(
                "A file inside a bundled Opik agent skill — `skill` is the skill name, "
                "`path` is a path relative to it (`SKILL.md`, `references/<file>.md`)."
            ),
            mimeType="text/markdown",
            **_META_KWARGS,
        )
        return types.ServerResult(
            types.ListResourceTemplatesResult(
                resourceTemplates=[*existing, template],
                **_CACHE_FIELDS,
            )
        )

    for handler in (list_resources, read_resource, list_templates):
        setattr(handler, _INSTALLED_MARKER, True)
    lowlevel.request_handlers[types.ListResourcesRequest] = list_resources
    lowlevel.request_handlers[types.ReadResourceRequest] = read_resource
    lowlevel.request_handlers[types.ListResourceTemplatesRequest] = list_templates
