from __future__ import annotations

import logging
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from opik_mcp.analytics import EVENT_AUTH_REJECTED, boot_props, track_event
from opik_mcp.analytics.environment import cached_call_context_env
from opik_mcp.analytics.events import bucket_path
from opik_mcp.config import Settings, get_settings
from opik_mcp.identity.context import (
    classify_bearer,
    inbound_authorization,
    inbound_mcp_session_id,
    inbound_workspace,
    resolved_workspace_name,
    settings_auth_mode,
)
from opik_mcp.identity.oauth import introspect_oauth_token
from opik_mcp.identity.store import (
    ResolvedIdentity,
    forget_validation,
    lookup_identity,
    lookup_validation,
    remember_identity,
    remember_session,
    remember_validation,
)
from opik_mcp.server.http.paths import _is_unauth_path

logger = logging.getLogger("opik_mcp")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Bearer-shape check, OAuth token validation, and per-request bearer-capture.

    Two bearer shapes, two contracts. An ``OAUTH_ACCESS_TOKEN_PREFIX``-prefixed
    OAuth token is validated against opik-backend's introspection endpoint on
    every request and answered with an ``invalid_token`` 401 when dead — the
    resource-server duty the MCP authorization spec puts on us, and the only
    signal a host has to refresh (OPIK-8252). Any other well-formed
    ``Authorization: Bearer …`` is an API key and is **not validated locally**:
    the full header value is captured into a ContextVar and forwarded verbatim
    on the outbound call to opik-backend (see :mod:`opik_mcp.identity.context`),
    whose ``AuthFilter`` is its single point of enforcement. Deployments where
    the backend enforces auth are protected end-to-end; OSS installs without
    backend auth are as open via MCP as via their own REST API.

    Missing/empty ``Authorization`` returns 401 with a ``WWW-Authenticate``
    header that points MCP hosts at
    ``/.well-known/oauth-protected-resource`` so they can bootstrap the
    OAuth dance per RFC 6750 + RFC 9728. Health probes and the metadata
    endpoint are exempt from auth.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        resource_metadata_url: str | None,
    ) -> None:
        super().__init__(app)
        self._resource_metadata_url = resource_metadata_url

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        # Discovery + bootstrap paths are unauthenticated by spec — RFC 9728
        # well-known metadata, OIDC/OAuth AS metadata, and the OAuth-flow
        # endpoints all run pre-credentials. Returning 401 on these would
        # break the host's discovery chain; many SDKs probe path-prefixed
        # variants too (``/.well-known/foo/mcp``, ``/mcp/.well-known/foo``),
        # so we accept the whole prefix.
        if _is_unauth_path(path):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth:
            return self._unauthorized()

        if not auth.lower().startswith("bearer ") or not auth[len("Bearer ") :].strip():
            # Require the canonical "Bearer <token>" shape — non-Bearer
            # schemes and empty tokens are rejected here rather than
            # forwarded, so the host gets the WWW-Authenticate hint and a
            # clean recovery path instead of an opaque upstream 401.
            return self._unauthorized()

        auth_mode, oauth_token = classify_bearer(auth)
        mcp_session_id = request.headers.get("mcp-session-id")
        # OAuth bearers are validated on EVERY request to the MCP path before
        # anything is forwarded (MCP authorization spec 2026-07-28, Token
        # Handling: the resource server MUST validate the access token and MUST
        # answer 401 for an invalid or expired one). That 401 is the only signal
        # a host has to run the ``refresh_token`` grant — a dead token that
        # reached opik-backend used to come back as a tool error inside HTTP 200,
        # and hosts kept a "connected" connector that could not make a call.
        # API-key bearers are forwarded untouched: opik-backend's AuthFilter is
        # their single point of enforcement.
        identity = None
        if auth_mode == "oauth":
            outcome = await self._validate_oauth_bearer(auth, oauth_token)
            if isinstance(outcome, Response):
                return outcome
            identity = outcome

        # Capture the inbound auth + workspace headers for the duration of
        # this request so the outbound :class:`OpikClient` can forward them.
        auth_token = inbound_authorization.set(auth)
        workspace = request.headers.get("comet-workspace")
        workspace_token = inbound_workspace.set(workspace)
        # TELEMETRY ONLY — see ``identity.context.inbound_mcp_session_id``. The
        # session id is the stable unit the hosted funnel needs: a client keeps it
        # across OAuth token refreshes, so an 8-hour session counts once instead of
        # once per hourly token mint.
        session_id_token = inbound_mcp_session_id.set(mcp_session_id)
        # The ContextVar above is NOT sufficient on its own, and the reason is
        # subtle enough to be worth stating: events are built in the MCP session
        # task, which is forked from the `initialize` request and whose context is
        # therefore frozen BEFORE any session id exists. Requests that do carry
        # `Mcp-Session-Id` build no events of their own, so the var is read as
        # `None` for the life of the session. `remember_session` below closes
        # that gap by keying the id to the credential instead; the ContextVar
        # still serves events emitted inside a request, such as `auth_rejected`.
        #
        # The OAuth-authorized workspace NAME feeds the per-session instructions
        # blob so an agent can truthfully say which workspace it operates against.
        # In OAuth mode the host sends no ``Comet-Workspace`` header, so this is
        # the only place the name is known.
        resolved_token = None
        if identity is not None and identity.workspace_name:
            resolved_token = resolved_workspace_name.set(identity.workspace_name)
        try:
            response = await call_next(request)
            if mcp_session_id is None:
                # The session-minting request: the id exists only on the way
                # out. Pair it with the credential now, because this is the one
                # moment both are in scope together.
                minted = response.headers.get("mcp-session-id")
                if minted:
                    remember_session(auth, minted)
            return response
        finally:
            inbound_authorization.reset(auth_token)
            inbound_workspace.reset(workspace_token)
            inbound_mcp_session_id.reset(session_id_token)
            if resolved_token is not None:
                resolved_workspace_name.reset(resolved_token)

    async def _validate_oauth_bearer(
        self, auth: str, oauth_token: str
    ) -> Response | ResolvedIdentity | None:
        """Validate an OAuth bearer: the 401 to send, or the identity it stands for.

        Cache first, opik-backend's introspection endpoint on a miss. A definite
        "invalid" (cached expiry, or a 401 from introspection) is answered with
        ``_invalid_token``; ``unknown`` (no REST base, network, 5xx) falls
        through and caches nothing — a backend hiccup must degrade to "forward
        as before", never to a mass logout. A "valid" answer is remembered for
        the configured TTL, capped at the backend's ``expires_at``. The identity
        (``None`` when the backend named nobody) is remembered against the
        token: the analytics layer builds events in the MCP session task and
        never sees a request, and a second handshake on the same token (host
        reconnect) reads it back instead of asking again.
        """
        settings = get_settings()
        verdict = lookup_validation(oauth_token)
        if verdict == "invalid":
            return self._invalid_token()
        if verdict == "valid":
            return lookup_identity(oauth_token)
        introspection = await introspect_oauth_token(auth, settings)
        if introspection.status == "invalid":
            forget_validation(oauth_token)
            return self._invalid_token()
        if introspection.status == "valid":
            remember_validation(
                oauth_token,
                ttl_s=settings.opik_mcp_oauth_validation_cache_ttl_s,
                expires_in_s=introspection.expires_in_s,
            )
            # RFC 8707 audience check, observe-only for now: the AS and opik-mcp
            # are configured independently, and a strict reject on a mismatch
            # would lock every host out in one deploy. Compared exactly as
            # configured — a trailing-slash difference is precisely what a
            # strict check would trip on, so the warning must show it.
            expected_resource = settings.opik_mcp_resource_uri
            if (
                introspection.resource
                and expected_resource
                and introspection.resource != expected_resource
            ):
                logger.warning(
                    "OAuth token bound to resource %r but this server is %r",
                    introspection.resource,
                    expected_resource,
                )
        if introspection.identity is not None:
            remember_identity(oauth_token, introspection.identity)
        return introspection.identity

    def _unauthorized(self) -> Response:
        # No credentials presented: RFC 6750 §3.1 says the challenge carries no
        # ``error`` parameter in that case. Pointing MCP hosts at protected-
        # resource metadata (RFC 9728) is what kicks off automatic OAuth
        # discovery — without it, hosts have no way to find the AS without
        # out-of-band config.
        return JSONResponse(
            {"error": "unauthorized"}, status_code=401, headers=self._challenge_headers()
        )

    def _invalid_token(self) -> Response:
        # A well-formed bearer that opik-backend no longer accepts. RFC 6750 §3.1
        # ``invalid_token`` is the code hosts key their refresh on: the token is
        # expired or revoked, re-run the ``refresh_token`` grant (or re-authorize
        # if that fails too) — as opposed to a bare 401 that reads as "start the
        # discovery dance from scratch".
        description = "The access token is invalid or expired"
        return JSONResponse(
            {"error": "invalid_token", "error_description": description},
            status_code=401,
            headers=self._challenge_headers(error="invalid_token", error_description=description),
        )

    def _challenge_headers(self, **params: str) -> dict[str, str]:
        """``WWW-Authenticate: Bearer …`` per RFC 6750 §3 + RFC 9728, or nothing.

        Omitted entirely when no resource-metadata URL is configured: a
        challenge that cannot point at the metadata gives a host nothing to act
        on, and some host parsers reject a bare/empty value outright.
        """
        if not self._resource_metadata_url:
            return {}
        parts = ['realm="opik-mcp"']
        parts.extend(f'{key}="{value}"' for key, value in params.items())
        parts.append(f'resource_metadata="{self._resource_metadata_url}"')
        return {"WWW-Authenticate": "Bearer " + ", ".join(parts)}


def _has_absolute_resource_metadata_url(settings: Settings) -> bool:
    """True only when ``OPIK_MCP_RESOURCE_URI`` is an absolute URL (scheme+netloc).

    ``_resource_metadata_url()`` returns a non-empty *relative* path even when the
    URI is unset, so ``bool()`` on it is always True. This answers the real
    question for BI: could a host actually bootstrap OAuth discovery from the
    ``WWW-Authenticate`` hint this rejection carried?
    """
    uri = settings.opik_mcp_resource_uri
    if not uri:
        return False
    parsed = urlparse(uri)
    return bool(parsed.scheme and parsed.netloc)


def _classify_rejection_reason(status_code: int, auth_header: str) -> str:
    """Map (status, Authorization-header shape) to a ``rejection_reason`` bucket.

    PRIVACY: inspects only the scheme keyword and whether a token is present —
    never stores or returns any part of the token value.
    """
    if status_code == 421:
        return "host_rejected"
    if status_code == 403:
        return "origin_rejected"
    # 401 — classify by header shape (mirrors BearerAuthMiddleware's checks).
    if not auth_header:
        return "missing_header"
    if not auth_header.lower().startswith("bearer "):
        return "not_bearer"
    if not auth_header[len("bearer ") :].strip():
        return "empty_token"
    # A well-formed bearer that still got a 401: an OAuth token opik-backend's
    # introspection reported dead (expired/revoked) — the refresh trigger for
    # hosts. Its own bucket (never echoes the token) so BI doesn't conflate it
    # with genuinely-missing-header rejections.
    return "token_rejected"


class AuthRejectionMiddleware:
    """Outermost ASGI wrapper: emit ``opik_mcp_auth_rejected`` for 401/421/403
    responses on authenticated paths (GAP#3).

    Pure ASGI (NOT ``BaseHTTPMiddleware``) so streaming SSE responses flow
    through unbuffered — it reads only the response status line. ``_UNAUTH_PATHS``
    (health, OAuth-proxy, discovery) are skipped: a 401 proxied from the AS
    during the OAuth dance is not opik-mcp's resource-server rejection, and
    counting it would pollute the auth-rejection chart. Non-http scopes
    (``lifespan``/``websocket``) pass straight through.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_holder: list[int] = []

        async def _capture(message: Message) -> None:
            # Record the FIRST response status only (ASGI spec sends exactly one
            # http.response.start; guard against a misbehaving app sending more).
            if message["type"] == "http.response.start" and not status_holder:
                status_holder.append(message["status"])
            await send(message)

        await self.app(scope, receive, _capture)

        if status_holder and status_holder[0] in (401, 403, 421):
            try:
                self._emit_rejection(scope, status_holder[0])
            except Exception:
                # Telemetry must never affect the (already-sent) response.
                logger.debug("auth_rejected emit failed", exc_info=True)

    def _emit_rejection(self, scope: Scope, status_code: int) -> None:
        path = scope.get("path", "")
        if _is_unauth_path(path):
            return
        auth_header = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                auth_header = value.decode("latin-1", "replace")
                break
        # auth_mode must be derived HERE from the header: this runs after
        # BearerAuthMiddleware reset the inbound-auth ContextVar, so
        # _build_event's per-request fallback would otherwise always be the
        # settings value (never the rejected bearer). Shape-only — no token kept.
        if auth_header:
            auth_mode, _token = classify_bearer(auth_header)
        else:
            # No credential: settings-derived mode (shared with auth_mode_at_boot
            # so an OAuth-only deploy reports "oauth", not "none").
            auth_mode = settings_auth_mode(
                has_api_key=bool(self._settings.opik_api_key),
                has_as_url=bool(self._settings.opik_mcp_as_url),
            )
        props = {
            "rejection_reason": _classify_rejection_reason(status_code, auth_header),
            "auth_mode": auth_mode,
            "path_bucket": bucket_path(path, self._settings.opik_mcp_http_path),
            "oauth_configured": boot_props.oauth_configured(self._settings),
            "resource_metadata_url_present": str(
                _has_absolute_resource_metadata_url(self._settings)
            ).lower(),
            # Env cohort so BI can separate CI/probe noise from real misconfigs.
            **cached_call_context_env(),
        }
        track_event(EVENT_AUTH_REJECTED, props)
