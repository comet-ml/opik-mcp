from __future__ import annotations

from urllib.parse import urlparse

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from opik_mcp.config import Settings, get_settings
from opik_mcp.server.http.paths import _PROTECTED_RESOURCE_METADATA_PATH, _PROXIED_OAUTH_PATHS


async def _oauth_protected_resource(_request: Request) -> JSONResponse:
    """RFC 9728 protected-resource metadata.

    Returned to MCP hosts so they can discover the Authorization Server and
    run the OAuth dance against it without needing the AS URL preconfigured.

    When ``OPIK_MCP_AS_URL`` is unset, the discovery doc is unavailable —
    this opik-mcp instance is then only useful with ``OPIK_API_KEY``-style
    bearers. Returning 503 makes the misconfiguration loud and steers
    operators to set ``OPIK_MCP_AS_URL``.
    """
    settings = get_settings()
    if not settings.opik_mcp_as_url:
        return JSONResponse({"error": "OPIK_MCP_AS_URL not configured"}, status_code=503)
    body: dict[str, str | list[str]] = {
        "authorization_servers": [settings.opik_mcp_as_url],
    }
    if settings.opik_mcp_resource_uri:
        body["resource"] = settings.opik_mcp_resource_uri
    return JSONResponse(body)


# Headers that must not be forwarded from inbound → outbound on the proxy
# path. ``host`` would override httpx's auto-set Host; ``content-length`` is
# recomputed from the body; hop-by-hop framing headers don't make sense to
# forward; and ``cookie`` is intentionally dropped because OAuth flows use
# the AS's own session cookies and we don't want to leak SDK cookies upstream.
_PROXY_DROP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "cookie"}
)

# Hop-by-hop response headers per RFC 7230 §6.1 — must not be forwarded back
# unchanged or httpx/Starlette's framing assumptions break.
_PROXY_DROP_RESPONSE_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)


async def _proxy_to_as(request: Request) -> Response:
    """Proxy AS-flow / discovery requests to the configured AS host.

    MCP host SDKs probe ``/register``, ``/authorize``, ``/.well-known/oauth-
    authorization-server`` etc. at the resource server's host before they
    have a token. In production opik-mcp sits behind the same edge as
    opik-backend so these paths route correctly without ceremony. Locally
    (or in any split-host deploy) we proxy them to the configured AS so
    the SDK sees a same-origin response — earlier attempts using HTTP 307
    redirects failed because some SDKs do not follow cross-origin OAuth-
    discovery redirects.

    Proxying preserves method, body, query string, and most headers, and
    returns the AS response inline. The proxied AS metadata still contains
    absolute opik-backend URLs in its endpoint fields (``authorization_
    endpoint``, ``token_endpoint``, etc.) — SDKs that use those directly
    talk to the AS over the network; SDKs that probe ``/register`` etc. at
    the RS root land here again and we proxy that too.

    Returns 503 when ``OPIK_MCP_AS_URL`` is unset — the only safe answer:
    we don't know where to send the probe, and silently 404'ing would
    mislead clients into thinking the resource doesn't support OAuth.
    """
    settings = get_settings()
    if not settings.opik_mcp_as_url:
        return JSONResponse({"error": "OPIK_MCP_AS_URL not configured"}, status_code=503)
    target_path = _PROXIED_OAUTH_PATHS.get(request.url.path)
    if target_path is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    qs = request.url.query
    target = f"{settings.opik_mcp_as_url.rstrip('/')}{target_path}"
    if qs:
        target = f"{target}?{qs}"
    body = await request.body()
    forwarded_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _PROXY_DROP_REQUEST_HEADERS
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        upstream = await client.request(
            method=request.method,
            url=target,
            headers=forwarded_headers,
            content=body,
        )
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _PROXY_DROP_RESPONSE_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


def _resource_metadata_url(settings: Settings) -> str | None:
    """Build the absolute URL we advertise in ``WWW-Authenticate``.

    The metadata route is registered at the application root, so the URL we
    advertise must match: derived from the resource URI's scheme + authority
    rather than appended under its path. RFC 9728 §3.1 permits both
    path-prefixed and host-relative forms; MCP hosts in practice follow the
    host-relative form, and our Starlette ``Route`` registration sits at
    ``/`` + the well-known path (not nested under ``/mcp``). Appending under
    the resource path produces a URL that 404s (or falls through to the MCP
    path's auth middleware and 401s), silently breaking host bootstrap.

    Falls back to the bare relative path when no public URI is configured —
    still useful for hosts that resolve relative to the 401 URL, though that
    pathway has the same authority as the request that triggered it.
    """
    if settings.opik_mcp_resource_uri:
        parsed = urlparse(settings.opik_mcp_resource_uri)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{_PROTECTED_RESOURCE_METADATA_PATH}"
    return _PROTECTED_RESOURCE_METADATA_PATH
