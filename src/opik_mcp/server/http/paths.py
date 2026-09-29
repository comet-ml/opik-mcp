from __future__ import annotations

# Probes are unauthenticated by design — Kubernetes liveness/readiness probes
# can't carry the bearer token and must remain reachable even when auth
# misconfiguration would otherwise return 401.
_HEALTH_PATHS = frozenset({"/health", "/health/ready"})

# Protected-resource metadata is the bootstrap entry point for the OAuth
# dance: MCP hosts fetch it (per RFC 9728) before they have any credentials,
# so it must be reachable without an Authorization header.
_PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"

# AS-discovery / OAuth-flow paths that MCP host SDKs probe on the resource
# server's host before they have a token. In production opik-mcp sits behind
# the same edge as opik-backend so these paths "just work" — but locally
# they're on different ports, so we redirect to the configured AS to keep
# the SDK's discovery chain unbroken. Anything not in this set or
# ``_HEALTH_PATHS`` requires a bearer.
_PROXIED_OAUTH_PATHS = {
    # AS metadata + OIDC fallback some SDKs probe before the protected-resource doc
    "/.well-known/oauth-authorization-server": "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration": "/.well-known/oauth-authorization-server",
    # OAuth 2.1 flow endpoints; SDK convention is to find them at the RS root
    "/register": "/oauth/register",
    "/authorize": "/oauth/authorize",
    "/token": "/oauth/token",
    "/revoke": "/oauth/revoke",
    "/oauth/register": "/oauth/register",
    "/oauth/authorize": "/oauth/authorize",
    "/oauth/token": "/oauth/token",
    "/oauth/revoke": "/oauth/revoke",
}

_UNAUTH_PATHS = (
    _HEALTH_PATHS
    | frozenset({_PROTECTED_RESOURCE_METADATA_PATH})
    | frozenset(_PROXIED_OAUTH_PATHS.keys())
)


def _is_unauth_path(path: str) -> bool:
    """Paths that bypass bearer auth: health, protected-resource metadata, the
    OAuth-flow proxy paths, and the path-prefixed ``.well-known`` variants some
    SDKs probe. Shared by ``BearerAuthMiddleware`` (skip the 401) and
    ``AuthRejectionMiddleware`` (don't attribute a proxied-AS 401 as our
    rejection) so the two can't drift.
    """
    return (
        path in _UNAUTH_PATHS
        or path.startswith("/.well-known/")
        or path.startswith("/mcp/.well-known/")
    )
