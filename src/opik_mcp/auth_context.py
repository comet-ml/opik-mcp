"""Per-request inbound-auth propagation for OAuth-passthrough mode.

When opik-mcp runs over HTTP transport with OAuth, the MCP host attaches
`Authorization: Bearer …` (an ``OAUTH_ACCESS_TOKEN_PREFIX``-prefixed token)
per RFC 6750 and opik-mcp's job is to
forward that bearer onward to opik-backend's data API verbatim. Permission
enforcement lives at the data API endpoint via `@RequiredPermissions`
annotations. OAuth bearers are additionally validated up front by
``BearerAuthMiddleware`` (introspection round-trip, ``invalid_token`` 401 when
dead — OPIK-8252); API-key bearers are not validated locally.

These ContextVars are set by ``BearerAuthMiddleware`` for the duration of
each inbound HTTP request and read by ``resolve_opik_config`` when the
outbound :class:`OpikClient` is constructed for that request. When unset
(stdio transport), the outbound client falls back to ``OPIK_API_KEY`` /
``COMET_WORKSPACE`` from settings.

ASGI runs every request in its own asyncio task, so ``ContextVar`` gives us
per-request isolation without threading anything through the call signatures
of the MCP tool implementations. One catch: a tool does NOT run in the request
task. The SDK forks the MCP session task from the ``initialize`` request, so
inside a tool these vars hold the handshake-time values — and an OAuth bearer
changes mid-session once the host refreshes it (OPIK-8252). ``server.
install_request_auth_rebinding`` therefore re-binds ``inbound_authorization``
and ``inbound_workspace`` on every ``tools/call`` from the request the SDK
attaches to its request context, so the outbound client forwards the bearer
of the request that is actually being served.
"""

from contextvars import ContextVar

# Access-token prefix minted by opik-backend (McpOAuthTokenUtils.ACCESS_PREFIX).
# OAuth-passthrough detection MUST match the issuer: a mismatch makes a real
# OAuth bearer fall through to the API-key path, which then forwards a stale
# Comet-Workspace header that opik-backend rejects with 403.
OAUTH_ACCESS_TOKEN_PREFIX = "opik_mcp_at_"

# Full inbound ``Authorization`` header value (an ``OAUTH_ACCESS_TOKEN_PREFIX``-prefixed
# bearer), forwarded verbatim on outbound calls to opik-backend's data API. ``None``
# means "no inbound bearer; fall back to settings.opik_api_key".
inbound_authorization: ContextVar[str | None] = ContextVar("inbound_authorization", default=None)

# Inbound ``Comet-Workspace`` header value, forwarded verbatim. opik-backend
# cross-checks this against the token row server-side (`McpOAuthService.
# verifyWorkspaceHeaderMatchesToken`) and rejects mismatches with 403 before
# any downstream call. ``None`` means "fall back to settings.comet_workspace".
inbound_workspace: ContextVar[str | None] = ContextVar("inbound_workspace", default=None)

# OAuth-authorized workspace *name*, resolved from the opaque bearer via
# ``oauth_identity.introspect_oauth_token`` (the same call that validates it).
# Consumed ONLY by the instructions blob (``instructions.render_instructions``)
# so an agent can truthfully name the workspace it is operating against. Kept
# deliberately separate from ``inbound_workspace`` so this read-only display
# value never leaks into the outbound ``Comet-Workspace`` header / data routing
# (which stays token-derived server-side). ``None`` means "not resolved; fall
# back to the static settings workspace".
resolved_workspace_name: ContextVar[str | None] = ContextVar(
    "resolved_workspace_name", default=None
)

# Inbound ``Mcp-Session-Id`` header value. TELEMETRY ONLY — never forwarded and
# never used for routing; the MCP SDK owns session lifecycle entirely.
#
# SCOPE — READ THIS BEFORE BUILDING ON IT. This is a SESSION grain, not a user
# grain, and it does NOT enable an adoption funnel. A session ends; a funnel needs
# a unit that outlives one. "Habit = active on 3+ distinct days" is unanswerable
# here for the same reason it was unanswerable with the token. **The adoption
# funnel needs the Comet login** (``user_id`` / ``user_id_kind='comet_user'``),
# which ``caller_identity`` already resolves and which is live on stdio today —
# hosted reads zero only because it runs 0.2.12, predating that work. The fix
# there is a deploy, not this field.
#
# What this IS good for, and why it is worth the two lines:
#
#  1. It removes a specific inversion. The OAuth access token lives ONE HOUR, and
#     a handshake recurs on every mint while a tool call does not — so token-keyed
#     ratios fell as usage rose. An 8-hour session minted ~8 "authorized +
#     connected" pairs and usually one "invoked". Measured over 30 days: 533 of
#     568 tokens died inside the TTL and invoked at 9.6%, against ~80% for the 35
#     that outlived it. The session id collapses those 8 back to 1.
#  2. It survives the identity gaps. When ``lookup_identity`` misses — pod
#     restart, LRU eviction — events fall back to the nil ``install_id`` and merge
#     into one row. A session digest still groups that session correctly.
#
# NOT SUFFICIENT ON ITS OWN — do not build on this var alone. Tool events are
# built in the MCP session task, which is forked from the ``initialize`` request
# and whose context is frozen BEFORE a session id exists; the later requests that
# do carry ``Mcp-Session-Id`` build no events of their own. So this var reads
# ``None`` for every tool event, which is why the field silently never appeared
# in production despite tests that set the var directly. The working path is
# ``credential_identity.remember_session`` / ``lookup_session_digest``, keyed by
# the credential — the only value in scope on both sides. This var still serves
# events emitted inside a request, such as ``auth_rejected``.
#
# ``None`` means stdio, or the session-creating request itself (the ``initialize``
# handshake carries no session id yet).
inbound_mcp_session_id: ContextVar[str | None] = ContextVar("inbound_mcp_session_id", default=None)


def classify_bearer(auth_header: str) -> tuple[str, str]:
    """Classify a non-empty inbound ``Authorization`` header for BI analytics.

    Returns ``(auth_mode, oauth_token)``:
    - ``("oauth", token)`` for an ``OAUTH_ACCESS_TOKEN_PREFIX``-prefixed bearer — the
      token is returned ONLY so the caller can hash it; never stored or emitted raw.
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


# What a tool error says when opik-backend answers 401 to a call made with an
# OAuth bearer. Worded for the MODEL, which is what reads tool errors: the
# token — not the user's configuration — is the problem, and the fix is to
# retry, because the retry is the request that meets ``BearerAuthMiddleware``'s
# ``invalid_token`` 401 and triggers the host's ``refresh_token`` grant. Telling
# the model to "reconnect" here made it send users to the settings page for a
# recovery the client would have done on its own (OPIK-8252).
OAUTH_TOKEN_EXPIRED_HINT = (
    "The Opik access token is expired or revoked. Retry this call — the MCP client "
    "refreshes the token on the next request."
)


def oauth_token_expired_hint() -> str | None:
    """The 401 hint for the credential this request is forwarding, or ``None``.

    Reads the inbound bearer for the current request: ``OAUTH_TOKEN_EXPIRED_HINT``
    when it is an OAuth token, ``None`` for an API key (or stdio, where there is
    no inbound bearer at all), so API-key callers keep their "check OPIK_API_KEY"
    guidance. Single source of truth for every layer that renders a backend 401
    — the read/list client, the write envelope — so the wording cannot drift.
    Pure: the cache eviction that goes with a backend 401 lives beside the HTTP
    call (``opik_client.note_backend_401``), not in a message helper.
    """
    auth = inbound_authorization.get()
    if not auth:
        return None
    mode, _ = classify_bearer(auth)
    return OAUTH_TOKEN_EXPIRED_HINT if mode == "oauth" else None
