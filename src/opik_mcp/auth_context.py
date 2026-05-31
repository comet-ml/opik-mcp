"""Per-request inbound-auth propagation for OAuth-passthrough mode.

When opik-mcp runs over HTTP transport with OAuth, the MCP host attaches
`Authorization: Bearer opik_at_…` per RFC 6750 and opik-mcp's job is to
forward that bearer onward to opik-backend's data API verbatim. Permission
enforcement lives at the data API endpoint via `@RequiredPermissions`
annotations; opik-mcp performs no local validation and makes no separate
validator round-trip.

These ContextVars are set by ``BearerAuthMiddleware`` for the duration of
each inbound HTTP request and read by ``resolve_opik_config`` when the
outbound :class:`OpikClient` is constructed for that request. When unset
(stdio transport, or HTTP transport in dev-token mode), the outbound client
falls back to ``OPIK_API_KEY`` / ``COMET_WORKSPACE`` from settings.

ASGI runs every request in its own asyncio task, so ``ContextVar`` gives us
per-request isolation without threading anything through the call signatures
of the MCP tool implementations.
"""

from contextvars import ContextVar

# Full inbound ``Authorization`` header value (e.g. ``"Bearer opik_at_…"``),
# forwarded verbatim on outbound calls to opik-backend's data API. ``None``
# means "no inbound bearer; fall back to settings.opik_api_key".
inbound_authorization: ContextVar[str | None] = ContextVar("inbound_authorization", default=None)

# Inbound ``Comet-Workspace`` header value, forwarded verbatim. opik-backend
# cross-checks this against the token row server-side (`McpOAuthService.
# verifyWorkspaceHeaderMatchesToken`) and rejects mismatches with 403 before
# any downstream call. ``None`` means "fall back to settings.comet_workspace".
inbound_workspace: ContextVar[str | None] = ContextVar("inbound_workspace", default=None)
