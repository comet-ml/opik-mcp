"""Resolve the identity an OAuth bearer stands for.

In OAuth-passthrough mode opik-mcp forwards an opaque ``opik_mcp_at_``-prefixed
bearer onward and lets opik-backend derive the workspace from the token row — it
never learns who the caller is (the host doesn't send ``Comet-Workspace`` in OAuth
mode; the identity lives only in the token binding). Two consumers need that
answer: the per-session ``initialize`` instructions blob (``instructions.py``),
so an agent can truthfully say which workspace it is operating against, and BI,
which cannot attribute a call without a user and a workspace.

opik-backend exposes a purpose-built introspection endpoint for exactly this —
``POST /opik/auth-oauth`` (``OAuthValidateTokenResource``) returns the full
``ValidatedToken``: ``user_name``, ``workspace_id`` and ``workspace_name``. Since
OPIK-8252 the same call is also how opik-mcp discharges its resource-server duty
(MCP authorization spec, Token Handling): ``BearerAuthMiddleware`` asks it on
every request carrying an OAuth bearer (cached, see ``credential_identity``)
and answers ``invalid_token`` 401 when the backend says the token is dead.

The outcome is three-way on purpose — see :data:`IntrospectionStatus`. A
definite 401 is the only rejection; any failure to get an answer (unconfigured
base, non-200, network error, malformed body) is ``unknown``, so the request
is forwarded as before and the blob falls back to the static workspace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

from opik_mcp.config import Settings
from opik_mcp.credential_identity import ResolvedIdentity
from opik_mcp.opik_client import opik_rest_base

logger = logging.getLogger("opik_mcp")

IntrospectionStatus = Literal["valid", "invalid", "unknown"]
"""Three-way outcome of asking opik-backend about a bearer.

``valid`` — 200, the token is live; ``invalid`` — 401, the token is unknown,
expired or revoked and the caller MUST be answered with HTTP 401 (MCP
authorization spec, Token Handling); ``unknown`` — we could not get an answer
(no REST base, network error, timeout, 5xx, malformed body). ``unknown`` is
deliberately distinct from ``invalid``: a backend hiccup must degrade to
"forward as before", never to a mass logout of every connected host.
"""


@dataclass(frozen=True, slots=True)
class Introspection:
    """What opik-backend said about an inbound OAuth bearer."""

    status: IntrospectionStatus
    identity: ResolvedIdentity | None = None
    resource: str | None = None
    # Seconds until the token expires, from the backend's ``expires_at`` (an
    # ISO-8601 instant). ``None`` when the backend did not report one — older
    # opik-backend releases don't, and the validation cache then falls back to
    # its TTL alone. Never negative: an already-past ``expires_at`` reads as 0.
    expires_in_s: float | None = None


# JAX-RS path of opik-backend's token-introspection endpoint
# (``OAuthConstants.OAUTH_VALIDATE_TOKEN_RESOURCE_BASE_PATH``). It is a sibling of
# the ``/v1/private/...`` REST routes at the backend root, so it hangs off the same
# REST base opik-mcp already uses for data calls.
_VALIDATE_TOKEN_PATH = "/opik/auth-oauth"


async def introspect_oauth_token(authorization: str, settings: Settings) -> Introspection:
    """Ask opik-backend whether an inbound OAuth bearer is live, and for whom.

    ``authorization`` is the full inbound ``Authorization`` header value
    (``"Bearer opik_mcp_at_…"``), forwarded verbatim — opik-backend re-validates
    the token shape and resolves it server-side. Never raises.

    A 200 is ``valid`` (with the identity when the body names anyone); a 401 is
    ``invalid``; everything else is ``unknown``. See :data:`IntrospectionStatus`
    for why the last two must not be conflated.
    """
    base = opik_rest_base(settings)
    if base is None:
        return Introspection(status="unknown")
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
        if resp.status_code == 401:
            return Introspection(status="invalid")
        if resp.status_code != 200:
            # WARNING, not DEBUG: an ``unknown`` here means the request is
            # forwarded UNVALIDATED (fail-open). A run of these in production is
            # the difference between "the token died inside the cache window"
            # and "the resource server cannot reach its introspection endpoint"
            # — invisible at the default INFO level otherwise.
            logger.warning(
                "OAuth token introspection failed open: %s %s returned %s",
                "POST",
                url,
                resp.status_code,
            )
            return Introspection(status="unknown")
        body = resp.json()
    except Exception as exc:
        # Must NEVER raise: this runs inside the auth middleware on every
        # request. Catch broadly on purpose — beyond httpx.HTTPError + the
        # ValueError from resp.json() on a non-JSON body, httpx raises
        # httpx.InvalidURL (a direct Exception subclass, NOT an HTTPError) for a
        # malformed REST base, which would otherwise 500 the request.
        logger.warning(
            "OAuth token introspection failed open: POST %s raised %s",
            url,
            type(exc).__name__,
        )
        return Introspection(status="unknown")
    if not isinstance(body, dict):
        # A 200 whose body is not the ValidatedToken object is malformed, and a
        # malformed answer is no answer: fail open and cache nothing.
        logger.warning(
            "OAuth token introspection failed open: POST %s returned a %s, not an object",
            url,
            type(body).__name__,
        )
        return Introspection(status="unknown")
    workspace_name = _text(body.get("workspace_name"))
    user_name = _text(body.get("user_name"))
    identity = None
    if workspace_name is not None or user_name is not None:
        # An identity with nothing in it is indistinguishable from an unresolved
        # one to every consumer, and storing it would only suppress a later retry.
        identity = ResolvedIdentity(
            user_name=user_name,
            workspace_name=workspace_name,
            workspace_id=_text(body.get("workspace_id")),
        )
    return Introspection(
        status="valid",
        identity=identity,
        resource=_text(body.get("resource")),
        expires_in_s=_seconds_until(_text(body.get("expires_at"))),
    )


def _seconds_until(expires_at: str | None) -> float | None:
    """Seconds from now until an ISO-8601 instant, floored at 0; ``None`` if absent/unparseable."""
    if expires_at is None:
        return None
    try:
        when = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        logger.debug("token introspection: unparseable expires_at %r", expires_at)
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _text(value: object) -> str | None:
    """A non-empty string from the introspection body, or ``None``.

    Absent, null, blank and wrongly-typed values collapse to ``None`` so every
    downstream consumer can treat "unknown" as one case. Emitting a blank string
    would land in BI as an empty value rather than a null.
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "Introspection",
    "IntrospectionStatus",
    "introspect_oauth_token",
]
