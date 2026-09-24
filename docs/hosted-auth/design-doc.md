# hosted-auth

## Purpose

hosted-auth is the HTTP app the Docker image serves. It checks the bearer on
each request, validates OAuth tokens against opik-backend, serves the OAuth
discovery documents and records who the caller is. Open it to learn what a host
sees when a token is missing, expired or an API key, and where to change that.

## What it does now

### Two kinds of bearer

Every request to the MCP path needs `Authorization: Bearer <token>`. A token
that starts with `OAUTH_ACCESS_TOKEN_PREFIX` (in `src/opik_mcp/auth_context.py`)
is an OAuth token. Any other bearer is an API key.

| | OAuth token | API key |
|---|---|---|
| Checked by opik-mcp | Introspected on every request, with a short cache | No |
| Forwarded to opik-backend | The full `Authorization` header | The full `Authorization` header |
| Workspace | Taken from the token by opik-backend; an inbound `Comet-Workspace` header is forwarded and checked against it | The inbound `Comet-Workspace` header, else `OPIK_WORKSPACE` (or `COMET_WORKSPACE`), else `default` |
| Dead credential | HTTP 401 `invalid_token`, so the host refreshes. A backend 401 on a data call first comes back as a tool error inside HTTP 200; the retry gets the 401 | A tool error inside HTTP 200 that says to check `OPIK_API_KEY` and `OPIK_WORKSPACE` |

Whether a hosted API-key call should fall back to `OPIK_WORKSPACE` is an open
question in [runtime](../runtime/design-doc.md#credential-and-workspace).

### What a host gets back

- Health, discovery and OAuth-flow paths need no auth, including anything
  under `/.well-known/` or `/mcp/.well-known/` (`_is_unauth_path`).
- A missing header, a scheme other than `Bearer` or an empty token gets 401
  `{"error": "unauthorized"}`.
- An OAuth token that opik-backend calls dead gets 401 `invalid_token`, and
  nothing is forwarded. Hosts run the `refresh_token` grant on this code.
- Both 401s carry a `WWW-Authenticate` challenge whose `resource_metadata`
  is the protected-resource URL at the host root of `OPIK_MCP_RESOURCE_URI`,
  or the relative path when that is unset (`_resource_metadata_url`). The served
  app always sends it. A URL under the resource path would hit the auth middleware (#139).
- A well-formed API key is never refused here. `initialize` succeeds, and the
  first tool call carries the backend's answer as a tool error.
- `/.well-known/oauth-protected-resource` serves the RFC 9728 document. The
  paths in `_PROXIED_OAUTH_PATHS` are proxied to `OPIK_MCP_AS_URL`. Both
  answer 503 when that setting is unset.
- `/health/ready` sends a HEAD to `COMET_URL_OVERRIDE` (the default Comet URL if empty).
  A 5xx, a timeout or a network error is not ready; a malformed URL is a 500 on purpose.

### OAuth validation and refresh

- The cache in `src/opik_mcp/credential_identity.py` is checked first. A miss
  posts the inbound header to `/opik/auth-oauth` under the Opik REST base
  (`introspect_oauth_token` in `src/opik_mcp/oauth_identity.py`).
- 200 is valid and 401 is invalid. Anything else (no REST base, network
  error, timeout, another status, a body that is not a JSON object) is
  unknown: the request is forwarded unchecked and nothing is cached. Each
  unknown case logs a WARNING, "failed open", except a missing REST base,
  which fails open silently.
- A valid answer is cached for `OPIK_MCP_OAUTH_VALIDATION_CACHE_TTL_S`, never
  past `expires_at` minus `EXPIRY_SKEW_MARGIN_S`. A cached token whose
  `expires_at` has passed gets 401 without a backend call.
- A `resource` that differs from `OPIK_MCP_RESOURCE_URI` is logged and served.
- A backend 401 on a data call made with an OAuth token runs
  `note_backend_401` in `src/opik_mcp/opik_client.py`. It drops the cached
  validation, and the tool error (`OAUTH_TOKEN_EXPIRED_HINT`) tells the model
  to retry. The retry gets the `invalid_token` 401 and the host refreshes.

### Workspace name and caller identity

opik-mcp never chooses an OAuth call's workspace. opik-backend rejects a forwarded
header that does not match the token (`McpOAuthService.verifyWorkspaceHeaderMatchesToken`,
per the comment on `inbound_workspace`). The introspected name is display-only
(`resolved_workspace_name`); its precedence is in [tool-surface](../tool-surface/design-doc.md).

Caller identity feeds analytics and never decides access. The rule is in
`caller_identity_with_outcome` (`src/opik_mcp/caller_identity.py`): an OAuth
token uses what introspection stored for it, an inbound API key is a miss, and
stdio resolves the install's own key through `src/opik_mcp/account_identity.py`
on cloud Comet only.

## How it works

```
host -> AuthRejectionMiddleware -> BearerAuthMiddleware
        (shape check, OAuth validation, ContextVars set)
     -> MCP session manager -> session task
     -> install_request_auth_rebinding (ContextVars from this request)
     -> tool -> resolve_opik_config -> OpikClient -> opik-backend
```

- Assembly of the app and settings: `build_app` in `src/opik_mcp/server.py`,
  the OAuth and HTTP fields of `Settings` in `src/opik_mcp/config.py`.
- Order of checks and the 401 bodies: `BearerAuthMiddleware.dispatch`.
- Validation and the cache: `_validate_oauth_bearer`, then the two modules above.
- A new proxied OAuth path: `_PROXIED_OAUTH_PATHS`.

Boundaries: [runtime](../runtime/design-doc.md) owns the outbound client and
`resolve_opik_config`, [analytics](../analytics/design-doc.md) the rejection
event and identity fields, [release](../release/design-doc.md) the image and
chart, [tool-surface](../tool-surface/design-doc.md) the instructions and links.

## Decisions

- opik-mcp mints no tokens and keeps no session store; opik-backend mints them. No ADR yet (OPIK-8497).
- API keys are forwarded unchecked; opik-backend's auth filter enforces them.
  OAuth tokens are introspected because the MCP authorization spec requires a
  401 for an expired token, the host's only signal to refresh. A dead token
  used to come back inside HTTP 200, and users lost the connector about an
  hour after connecting (OPIK-8252, #182).
- Introspection fails open, so a backend outage does not log out every host.
  Only valid answers are cached, so a refreshed token works on first use (#182).
- Credentials are bound again on every `tools/call`, because the SDK forks the
  session task from `initialize` and a tool would forward the handshake's token
  after a refresh (#182).
- The introspected workspace name has its own ContextVar so it cannot reach
  the outbound `Comet-Workspace` header (#151).
- OAuth paths are proxied instead of redirected, because some host SDKs do not
  follow cross-origin redirects during discovery (#139).
- The audience check only logs. The AS and opik-mcp are configured
  separately, and a strict check could lock every host out in one deploy (#182).
- Credentials are kept only as SHA-256 digests in LRU maps, so the stores never hold a bearer in plaintext (#161).

### Traps

- OAuth detection exists twice, in `classify_bearer` and again inside
  `resolve_opik_config`. Both must match opik-backend's prefix. On a mismatch
  a real OAuth token takes the API-key path and the backend answers 403.
- The validation and identity stores are keyed by the bare token that
  `classify_bearer` returns. The session store is keyed by the full
  `Authorization` header. A lookup with the other input silently misses.
- `_validate_oauth_bearer` caches a valid answer before it compares
  `resource`. A rejecting audience check has to run before
  `remember_validation`, or the next request is served from cache.

## Proven by

- Shape checks, the 401 challenge and API keys initializing:
  `tests/test_http_auth.py`, `tests/test_oauth_passthrough_mode.py`.
- An API key keeps its wording on a backend 401:
  `test_upstream_401_in_api_key_mode_keeps_the_api_key_wording`.
- `invalid_token`, fail-open, the cache and its eviction:
  `tests/test_oauth_token_validation.py`, `tests/test_oauth_identity.py`.
- A refreshed token is the one forwarded: `tests/test_oauth_token_rotation.py`.
- Prefix detection: `test_resolve_opik_config_oauth_detection_is_prefix_not_substring`.
- Metadata, proxy and path bypass: `tests/test_oauth_protected_resource.py`,
  `tests/test_resource_metadata_url.py`, `tests/test_oauth_redirect.py`.
- HTTP path, allow-lists, health: `tests/test_http_path_config.py`, `tests/test_health.py`.
- Digest keys, LRU bounds, session pairing: `tests/test_credential_identity.py`.
- An inbound bearer never takes the operator's identity:
  `test_a_forwarded_api_key_bearer_is_not_resolved_as_our_own`;
  the install's own key: `tests/test_account_identity.py`.
- Rejection buckets and skipped paths: `tests/test_analytics_auth_rejected.py`.

## Log

- 2026-09-23: the introspected workspace name also feeds UI links; no link when an OAuth caller's workspace is unknown (#201).
- 2026-09-04: expired OAuth tokens get 401 `invalid_token`, with a validation cache, so hosts refresh (#182).
- 2026-08-13: caller identity resolved for OAuth tokens and API keys, so events name the user (#161).
- 2026-06-18: OAuth detection matches the backend's token prefix, so real OAuth tokens take the OAuth path (#149).
- 2026-06-04: configurable transport path and Host and Origin allow-lists, for the hosted deployment (#143, #147).
- 2026-06-03: OAuth passthrough, RFC 9728 metadata and the AS proxy; dev-token mode removed, for hosted OAuth (#139).
