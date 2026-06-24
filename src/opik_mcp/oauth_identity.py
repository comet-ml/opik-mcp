"""Resolve the OAuth-authorized workspace name for the session-context blob.

In OAuth-passthrough mode opik-mcp forwards an opaque ``opik_mcp_at_``-prefixed
bearer onward and lets opik-backend derive the workspace from the token row — it
never learns the workspace *name* itself (the host doesn't send ``Comet-Workspace``
in OAuth mode; the name lives only in the token binding). That's fine for data
routing, but the per-session ``initialize`` instructions blob (``instructions.py``)
needs the human-readable name so an agent can truthfully say which workspace it is
operating against.

opik-backend exposes a purpose-built introspection endpoint for exactly this —
``POST /opik/auth-oauth`` (``OAuthValidateTokenResource``) returns the identity a
bearer resolves to, including ``workspace_name``. We call it once per session, on
the ``initialize`` handshake, forwarding the inbound bearer verbatim.

This is best-effort: any failure (unconfigured base, non-200, network error,
malformed body) returns ``None`` so the handshake never breaks — the blob simply
falls back to the static settings workspace.
"""

from __future__ import annotations

import logging

import httpx

from opik_mcp.config import Settings
from opik_mcp.opik_client import opik_rest_base

logger = logging.getLogger("opik_mcp")

# JAX-RS path of opik-backend's token-introspection endpoint
# (``OAuthConstants.OAUTH_VALIDATE_TOKEN_RESOURCE_BASE_PATH``). It is a sibling of
# the ``/v1/private/...`` REST routes at the backend root, so it hangs off the same
# REST base opik-mcp already uses for data calls.
_VALIDATE_TOKEN_PATH = "/opik/auth-oauth"


async def resolve_workspace_name(authorization: str, settings: Settings) -> str | None:
    """Introspect an inbound OAuth bearer → its ``workspace_name``, or ``None``.

    ``authorization`` is the full inbound ``Authorization`` header value
    (``"Bearer opik_mcp_at_…"``), forwarded verbatim — opik-backend re-validates
    the token shape and resolves it server-side. Never raises; returns ``None`` on
    any failure so the ``initialize`` handshake degrades gracefully.
    """
    base = opik_rest_base(settings)
    if base is None:
        return None
    url = f"{base}{_VALIDATE_TOKEN_PATH}"
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.opik_mcp_oauth_introspect_timeout_s
        ) as client:
            resp = await client.post(url, headers=headers)
        if resp.status_code != 200:
            logger.debug("workspace introspection: non-200 status %s", resp.status_code)
            return None
        body = resp.json()
    except Exception:
        # Best-effort by contract: this runs on the ``initialize`` handshake and
        # must NEVER raise (a failure just falls back to the static workspace).
        # Catch broadly on purpose — beyond httpx.HTTPError + the ValueError from
        # resp.json() on a non-JSON body, httpx raises httpx.InvalidURL (a direct
        # Exception subclass, NOT an HTTPError) for a malformed REST base, which
        # would otherwise escape into the middleware and 500 the handshake.
        # Telemetry-free debug only — a failed lookup isn't worth surfacing.
        logger.debug("workspace introspection failed", exc_info=True)
        return None
    if not isinstance(body, dict):
        return None
    workspace_name = body.get("workspace_name")
    if isinstance(workspace_name, str) and workspace_name:
        return workspace_name
    return None


__all__ = ["resolve_workspace_name"]
