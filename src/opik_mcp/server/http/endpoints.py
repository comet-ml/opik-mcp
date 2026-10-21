from __future__ import annotations

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from opik_mcp.config import get_settings

# Bounded total budget for the upstream reachability check used by
# /health/ready. Probes run every 5-10s; a hung upstream must not stall the
# probe past the probe's own timeout, or readiness flips to "unknown" instead
# of "not ready" and traffic keeps flowing.
_READY_PROBE_TIMEOUT_S = 2.0


async def _liveness(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _readiness(_request: Request) -> JSONResponse:
    """Probe comet-backend reachability; fail closed.

    The MCP server is a thin relay to comet-backend / opik-backend, so
    "ready to serve" reduces to "upstream is reachable from this pod". A
    HEAD against the configured Comet base accepts any non-5xx as evidence
    the host is up — 4xx still means the TCP+TLS+HTTP stack works, which is
    what readiness actually cares about.

    The HTTP client is built per-request (no module-level singleton) so DNS
    changes — e.g., a comet-backend service IP rotation in Kubernetes —
    take effect on the next probe without a pod restart. Cost is negligible
    at probe frequency.
    """
    base = get_settings().comet_url_override.rstrip("/") or "https://www.comet.com"
    reason: str
    try:
        async with httpx.AsyncClient(timeout=_READY_PROBE_TIMEOUT_S) as client:
            resp = await client.head(base, follow_redirects=False)
    except httpx.TimeoutException:
        reason = "timeout"
    except httpx.NetworkError:
        # Genuine network failures only: ConnectError, ReadError, WriteError,
        # CloseError. Config bugs (InvalidURL, UnsupportedProtocol) are NOT
        # NetworkError and intentionally bubble to a 500 so a typo in
        # COMET_URL_OVERRIDE surfaces loudly instead of pinning the pod to
        # not_ready/network_error forever.
        reason = "network_error"
    else:
        if resp.status_code >= 500:
            reason = "upstream_5xx"
        else:
            return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not_ready", "reason": reason}, status_code=503)


async def _not_found_json(scope: Scope, receive: Receive, send: Send) -> None:
    """Default-route handler — returns 404 as JSON instead of ``text/plain``.

    Starlette's stock 404 is ``Content-Type: text/plain`` with body ``Not
    Found``. MCP host SDKs that JSON-parse every response (including
    discovery probes that legitimately end in 404) choke on the plain
    text and abort their bootstrap with "Failed to parse JSON". Returning
    a tiny JSON envelope keeps every error response on the canonical
    content-type contract.
    """
    response = JSONResponse({"error": "not_found"}, status_code=404)
    await response(scope, receive, send)
