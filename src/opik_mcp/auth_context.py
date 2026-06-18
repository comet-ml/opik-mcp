"""Per-request inbound-auth propagation for OAuth-passthrough mode.

When opik-mcp runs over HTTP transport with OAuth, the MCP host attaches
`Authorization: Bearer opik_mcp_at_…` per RFC 6750 and opik-mcp's job is to
forward that bearer onward to opik-backend's data API verbatim. Permission
enforcement lives at the data API endpoint via `@RequiredPermissions`
annotations; opik-mcp performs no local validation and makes no separate
validator round-trip.

These ContextVars are set by ``BearerAuthMiddleware`` for the duration of
each inbound HTTP request and read by ``resolve_opik_config`` when the
outbound :class:`OpikClient` is constructed for that request. When unset
(stdio transport), the outbound client falls back to ``OPIK_API_KEY`` /
``COMET_WORKSPACE`` from settings.

ASGI runs every request in its own asyncio task, so ``ContextVar`` gives us
per-request isolation without threading anything through the call signatures
of the MCP tool implementations.
"""

from contextvars import ContextVar

# Access-token prefix minted by opik-backend (McpOAuthTokenUtils.ACCESS_PREFIX).
# OAuth-passthrough detection MUST match the issuer: a mismatch makes a real
# OAuth bearer fall through to the API-key path, which then forwards a stale
# Comet-Workspace header that opik-backend rejects with 403.
OAUTH_ACCESS_TOKEN_PREFIX = "opik_mcp_at_"

# Full inbound ``Authorization`` header value (e.g. ``"Bearer opik_mcp_at_…"``),
# forwarded verbatim on outbound calls to opik-backend's data API. ``None``
# means "no inbound bearer; fall back to settings.opik_api_key".
inbound_authorization: ContextVar[str | None] = ContextVar("inbound_authorization", default=None)

# Inbound ``Comet-Workspace`` header value, forwarded verbatim. opik-backend
# cross-checks this against the token row server-side (`McpOAuthService.
# verifyWorkspaceHeaderMatchesToken`) and rejects mismatches with 403 before
# any downstream call. ``None`` means "fall back to settings.comet_workspace".
inbound_workspace: ContextVar[str | None] = ContextVar("inbound_workspace", default=None)


def classify_bearer(auth_header: str) -> tuple[str, str]:
    """Classify a non-empty inbound ``Authorization`` header for BI analytics.

    Returns ``(auth_mode, oauth_token)``:
    - ``("oauth", "<opik_mcp_at_…>")`` for an OAuth bearer — the token is returned
      ONLY so the caller can hash it; it is never stored or emitted raw.
    - ``("api_key", "")`` for any other forwarded credential (the token is NOT
      returned — api-key-shaped credentials are not hashed here).

    Mirrors ``opik_client.resolve_opik_config``'s OAuth detection
    (``partition(" ")`` + ``lstrip`` + ``OAUTH_ACCESS_TOKEN_PREFIX``) so BI's
    ``auth_mode`` / ``token_sha256`` agree with the credential actually forwarded
    outbound. Single source of truth shared by ``analytics.client._build_event``
    and ``server.AuthRejectionMiddleware`` so the two cannot drift.
    """
    scheme, _, token_raw = auth_header.partition(" ")
    token = token_raw.lstrip()
    if scheme.lower() == "bearer" and token.startswith(OAUTH_ACCESS_TOKEN_PREFIX):
        return "oauth", token
    return "api_key", ""


def settings_auth_mode(*, has_api_key: bool, has_as_url: bool) -> str:
    """Settings-derived ``auth_mode`` when there is no inbound credential.

    The mode an outbound Opik call would use by default: a static ``OPIK_API_KEY``
    ("api_key") wins; else a configured AS ("oauth"); else "none". Single source
    of truth shared by ``boot_props.auth_mode_at_boot`` (lifecycle events) and the
    no-credential fallback in ``client._build_event`` / ``AuthRejectionMiddleware``
    so per-call and boot events agree for OAuth-only deployments.
    """
    if has_api_key:
        return "api_key"
    if has_as_url:
        return "oauth"
    return "none"
