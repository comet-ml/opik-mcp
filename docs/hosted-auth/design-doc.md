# hosted-auth

## Purpose

hosted-auth is the HTTP app the Docker image serves: it checks the bearer on
each request, validates OAuth tokens, serves the discovery documents a host
needs to start OAuth, and works out who the caller is. Open this doc to find
out how a request with an OAuth token gets its workspace, how that differs
from an API key, and what a host sees when a token is missing or expired.

## What it does now

### Two kinds of bearer

Every request to the MCP path must carry `Authorization: Bearer <token>`.
`classify_bearer` in `src/opik_mcp/auth_context.py` splits bearers into two
kinds by prefix. A token that starts with `OAUTH_ACCESS_TOKEN_PREFIX` (the
prefix opik-backend puts on the MCP access tokens it mints) is an OAuth
token. Any other bearer is treated as an API key. The prefix has to match the
backend's: with a mismatch, a real OAuth token takes the API-key path, which
forwards a stale `Comet-Workspace` header that the backend rejects (comment on
`OAUTH_ACCESS_TOKEN_PREFIX`).

| | OAuth token | API key |
|---|---|---|
| Checked by opik-mcp | Yes, by introspection on every request, with a short cache | No |
| Forwarded to opik-backend | Verbatim, as the full `Authorization` header | Verbatim, as the full `Authorization` header |
| Workspace sent upstream | The inbound `Comet-Workspace` header if the host sent one, else none. The backend takes the workspace from the token. | The inbound `Comet-Workspace` header, else the configured `OPIK_WORKSPACE`, else `default` |
| Who the caller is | The introspection answer, stored against the token | Unknown on the hosted server |
| Dead credential | HTTP 401 `invalid_token`, so the host refreshes | A tool error inside HTTP 200 that says to check `OPIK_API_KEY` and `OPIK_WORKSPACE` |

The workspace rules are in `resolve_opik_config` in
`src/opik_mcp/opik_client.py`. That function belongs to
[runtime](../runtime/design-doc.md); this doc covers only how the values it
reads get set.

Unverified: whether a hosted request that sends an API key and no
`Comet-Workspace` header should use the process's `OPIK_WORKSPACE`. The code
does. `.claude/rules/security.md` says never to fall back to an environment
default when the caller supplied one; that covers the key, and whether it
covers a missing workspace header is open (the same point is in
[runtime](../runtime/design-doc.md#which-credential-and-workspace-a-call-uses)).

### What a host sees at the HTTP layer

`BearerAuthMiddleware.dispatch` in `src/opik_mcp/server.py` answers, in order:

1. Health, discovery and OAuth-flow paths pass through with no auth
   (`_is_unauth_path`). That includes any path under `/.well-known/` or
   `/mcp/.well-known/`, because some SDKs probe path-prefixed variants.
2. A missing header, a scheme other than `Bearer`, or an empty token gets 401
   `{"error": "unauthorized"}` (`_unauthorized`).
3. An OAuth token is validated (next section). A dead one gets 401
   `{"error": "invalid_token", ...}` (`_invalid_token`), and nothing is
   forwarded.
4. Anything else goes on to the MCP app.

Both 401s carry `WWW-Authenticate: Bearer realm="opik-mcp", ...,
resource_metadata="<url>"` so the host can find the protected-resource
metadata (`_challenge_headers`). With no metadata URL configured the header
is left out, because a challenge that points nowhere gives the host nothing to
act on. The `invalid_token` form adds `error` and `error_description`, which is
what hosts use to decide to run the `refresh_token` grant instead of starting
discovery again.

An API key that the backend rejects never becomes an HTTP 401 from this
server. `initialize` makes no backend call, so it succeeds for any well-formed
API-key bearer (`test_api_key_bearer_initializes`). The first tool call then
gets a tool error with the API-key wording
(`test_upstream_401_in_api_key_mode_keeps_the_api_key_wording`).

The MCP SDK's transport security answers a disallowed `Host` with 421 and a
disallowed `Origin` with 403 before a request reaches the tools.
`_classify_rejection_reason` names those buckets for the rejection event; the
event itself is [analytics](../analytics/design-doc.md).

### OAuth validation

`BearerAuthMiddleware._validate_oauth_bearer` validates each OAuth token:

1. It looks up the cache (`lookup_validation` in
   `src/opik_mcp/credential_identity.py`). "Valid" skips the backend and reads
   the stored identity. "Invalid" means the backend earlier reported an expiry
   that has now passed, and the answer is 401 at once.
2. On a miss it calls `introspect_oauth_token` in
   `src/opik_mcp/oauth_identity.py`. That posts the inbound `Authorization`
   header to opik-backend's `/opik/auth-oauth` under the Opik REST base, with
   `OPIK_MCP_OAUTH_INTROSPECT_TIMEOUT_S` as the timeout.
3. The answer is one of three:
   - `valid` (HTTP 200): the token is live. The body can name `user_name`,
     `workspace_name`, `workspace_id`, `resource` and `expires_at`.
   - `invalid` (HTTP 401): the token is unknown, expired or revoked. The
     cache entry is dropped and the host gets 401 `invalid_token`.
   - `unknown` (no REST base, network error, timeout, any other status, a
     body that is not a JSON object): the request is forwarded as if
     validation had not run, and nothing is cached. Each case is logged at
     WARNING as "failed open".
4. A `valid` answer is cached for `OPIK_MCP_OAUTH_VALIDATION_CACHE_TTL_S`,
   but only until `expires_at` minus a skew margin (`remember_validation`).
   An entry that would expire at once is not stored. Only valid answers are
   cached, so a freshly refreshed token works on its first use.
5. If the backend's `resource` differs from `OPIK_MCP_RESOURCE_URI`, the
   server logs a warning and still serves the request. The RFC 8707 audience
   check only logs for now.

When a data call made with an OAuth token gets 401 from the backend,
`note_backend_401` in `src/opik_mcp/opik_client.py` drops the cached
validation, so the next request is introspected and gets `invalid_token`
without waiting out the TTL. The tool error for that call tells the model to
retry, because the host refreshes the token on the next request
(`OAUTH_TOKEN_EXPIRED_HINT` in `src/opik_mcp/auth_context.py`).

### How an OAuth request gets its workspace

The host sends no `Comet-Workspace` header in OAuth mode. opik-mcp forwards
the token, and opik-backend works out the workspace from the token row and
cross-checks any `Comet-Workspace` header against it (comment on
`inbound_workspace`). opik-mcp never picks the workspace for an OAuth call.

The workspace name from introspection is used only for display. The
middleware sets it into the `resolved_workspace_name` ContextVar, which the
per-session `initialize` instructions (`src/opik_mcp/instructions.py`) and
the UI links (`src/opik_mcp/read_list/ui_links.py`) read. It never goes into
the outbound `Comet-Workspace` header. When introspection named no workspace,
the instructions fall back to the configured workspace, and `link_workspace`
in `src/opik_mcp/read_list/ui_links.py` omits the link, since a guessed
workspace would link into the wrong one.

### Per-request credentials

The middleware copies the inbound `Authorization` and `Comet-Workspace`
headers into the ContextVars `inbound_authorization` and `inbound_workspace`
(`src/opik_mcp/auth_context.py`) for the length of the request, and resets
them afterwards. `resolve_opik_config` reads them when it builds the outbound
client, so a tool call uses its caller's credential and nobody else's.

A tool does not run in the request's task. The MCP SDK forks the session task
from the `initialize` request, so inside a tool the ContextVars still hold the
handshake's values. After a refresh, the host sends a new token on the same
session. Left alone, the server would forward the old, dead token.
`install_request_auth_rebinding` in `src/opik_mcp/server.py` wraps the
`tools/call` handler and sets both ContextVars again from the HTTP request
that carries that call. On stdio there is no HTTP request and the wrapper does
nothing.

On stdio there is no inbound header at all, and `resolve_opik_config` uses
`OPIK_API_KEY` and `OPIK_WORKSPACE` from settings. On HTTP a request without a
bearer never reaches a tool, so the settings key is not used in place of a
missing caller credential.

### Discovery and the authorization-server proxy

- `GET /.well-known/oauth-protected-resource` (`_oauth_protected_resource`)
  returns the RFC 9728 document: `authorization_servers` from
  `OPIK_MCP_AS_URL` and, when set, `resource` from `OPIK_MCP_RESOURCE_URI`.
  With no AS URL it returns 503 so the missing setting is visible.
- The URL advertised in `WWW-Authenticate` (`_resource_metadata_url`) is the
  scheme and host of `OPIK_MCP_RESOURCE_URI` plus the well-known path, with
  any path on the resource URI dropped, because the route is registered at the
  root. Without a usable resource URI it falls back to the relative path.
- Authorization-server discovery and flow paths (`/.well-known/oauth-authorization-server`,
  `/.well-known/openid-configuration`, `/register`, `/authorize`, `/token`,
  `/revoke` and their `/oauth/...` forms, listed in `_PROXIED_OAUTH_PATHS`)
  are proxied to `OPIK_MCP_AS_URL` by `_proxy_to_as`. The proxy keeps the
  method, body and query string, drops `host`, `content-length`, framing
  headers and `cookie`, and returns the AS response as is. With no AS URL it
  returns 503. The OIDC path maps to the AS's OAuth metadata.
- Unknown paths get a JSON 404 (`_not_found_json`), since some host SDKs parse
  every response as JSON and abort on a plain-text 404.

### Health probes

- `GET /health` (`_liveness`) returns `{"status": "ok"}` with no auth.
- `GET /health/ready` (`_readiness`) sends a HEAD to `COMET_URL_OVERRIDE`
  with a short timeout. Any answer below 500 is ready. A 5xx, a timeout or a
  network error gives 503 with `reason` set to `upstream_5xx`, `timeout` or
  `network_error`. A malformed URL is not caught and becomes a 500, so a typo
  in the setting shows up.

### Who the caller is

Identity is used for analytics and for naming the workspace: the middleware
sets `resolved_workspace_name` from it, which the instructions and UI links
read (`BearerAuthMiddleware.dispatch` in `src/opik_mcp/server.py`,
`src/opik_mcp/read_list/ui_links.py`). It never decides access: the OAuth 401
comes from token validation, and an API key is checked by the backend only
(the comment in `BearerAuthMiddleware.dispatch`). Three modules resolve it:

- `src/opik_mcp/oauth_identity.py` reads it from the introspection answer.
  The middleware stores it against the token (`remember_identity`), so later
  events in the session and a second handshake on the same token can read it
  without asking again.
- `src/opik_mcp/account_identity.py` resolves the install's own
  `OPIK_API_KEY` against Comet's `/api/rest/v2/account-details`
  (`resolve_api_key_identity`). It only runs against cloud Comet
  (`installation_type` in `src/opik_mcp/config.py`) and returns at once from
  memory or a disk cache under the user's home. A miss or a stale entry
  starts a background refresh. Refreshes are limited to one in flight per
  key, with a floor between attempts. Every failure ends as "unknown".
- `src/opik_mcp/caller_identity.py` decides which of the two applies
  (`caller_identity_with_outcome`). With an inbound OAuth token it uses only
  what was stored for that token. With an inbound API key it reports a miss,
  because there is no way to resolve an inbound API key today. With no inbound
  credential (stdio) it uses the install's own key. It never falls back to
  the server's own identity for an inbound bearer, since that would credit
  the caller's work to the operator.

`src/opik_mcp/credential_identity.py` holds the stores: identity per
credential, the MCP session id minted on each credential's handshake
(`remember_session`), and the OAuth validation cache. Every key is a SHA-256
digest of the credential (`credential_digest`), and session ids are stored
hashed too. Each store is an in-process LRU capped at
`MAX_TRACKED_CREDENTIALS`. Nothing survives a restart except the API-key disk
cache.

### Configuration

The OAuth and HTTP fields of `Settings` in `src/opik_mcp/config.py`:

| Env var | Default | Effect |
|---|---|---|
| `OPIK_MCP_AS_URL` | unset | Authorization server for the metadata and the proxy. Unset: both answer 503. |
| `OPIK_MCP_RESOURCE_URI` | unset | Public URI of this server; `resource` in the metadata, the base of the challenge URL, the expected audience. |
| `OPIK_MCP_OAUTH_INTROSPECT_TIMEOUT_S` | `5.0` | Timeout of one introspection call. |
| `OPIK_MCP_OAUTH_VALIDATION_CACHE_TTL_S` | `30.0` | How long a `valid` answer is trusted. |
| `OPIK_MCP_HOST` | `127.0.0.1` | Bind address. Loopback unless set. |
| `OPIK_MCP_PORT` | `8080` | Bind port. |
| `OPIK_MCP_HTTP_PATH` | `/mcp` | Path of the MCP transport. Must start with `/`. Behind a proxy that cannot rewrite paths, set it to the path of `OPIK_MCP_RESOURCE_URI`. |
| `OPIK_MCP_DNS_REBINDING_PROTECTION` | on | The SDK's Host and Origin check. |
| `OPIK_MCP_ALLOWED_HOSTS` | loopback hosts, any port | Comma-separated. A public deployment adds its host. |
| `OPIK_MCP_ALLOWED_ORIGINS` | loopback origins, any port | Comma-separated. Browser hosts need their origin here; CLI and desktop hosts send none. |

The process refuses to start with an AS URL and no resource URI; that check
and the bind preflight are in `src/opik_mcp/__main__.py`
([runtime](../runtime/design-doc.md)).

## How it works

`build_app` in `src/opik_mcp/server.py` builds the ASGI app that
`python -m opik_mcp` serves over Streamable HTTP. The transport choice is
runtime's. `build_app`:

1. installs the per-session hooks on the FastMCP server, including
   `install_session_instructions` (instructions rendered per session, owned by
   [tool-surface](../tool-surface/design-doc.md)) and
   `install_request_auth_rebinding`;
2. sets the transport path from `OPIK_MCP_HTTP_PATH` and transport security
   from the allowed hosts and origins;
3. adds `/health`, `/health/ready`, the protected-resource metadata route and
   one proxy route per `_PROXIED_OAUTH_PATHS` entry, and the JSON 404;
4. adds `BearerAuthMiddleware` with the advertised metadata URL;
5. composes the lifespan (runtime and analytics own it);
6. wraps everything in `AuthRejectionMiddleware`, a plain ASGI wrapper that
   reads only the response status so SSE streams are not buffered, and emits
   the rejection event for 401, 403 and 421 outside the unauthenticated paths.

A tool call on HTTP:

```
host -> AuthRejectionMiddleware -> BearerAuthMiddleware
        (shape check, OAuth validation, ContextVars set)
     -> MCP session manager -> session task
     -> install_request_auth_rebinding (ContextVars from this request)
     -> tool -> resolve_opik_config -> OpikClient -> opik-backend
```

Namespaces owned: `src/opik_mcp/auth_context.py`,
`src/opik_mcp/oauth_identity.py`, `src/opik_mcp/account_identity.py`,
`src/opik_mcp/caller_identity.py`, `src/opik_mcp/credential_identity.py`;
in `src/opik_mcp/server.py` the middleware classes, `_liveness`,
`_readiness`, `_oauth_protected_resource`, `_proxy_to_as`,
`_resource_metadata_url`, `install_request_auth_rebinding` and `build_app`;
the OAuth and HTTP fields of `src/opik_mcp/config.py`.

Boundaries: the outbound client, `resolve_opik_config` and the connection per
tool call are [runtime](../runtime/design-doc.md). Events, including
`opik_mcp_auth_rejected` and the identity fields on events, are
[analytics](../analytics/design-doc.md). The Dockerfile and Helm chart are
[release](../release/design-doc.md). The instructions text and UI links that
show the workspace name are [tool-surface](../tool-surface/design-doc.md).

## Decisions

- opik-mcp mints no tokens and keeps no session store of its own. OAuth
  tokens are opaque and minted by opik-backend, and opik-mcp checks them
  against the backend's existing `/opik/auth-oauth` endpoint. Its only state
  is the in-process caches in `src/opik_mcp/credential_identity.py`.
- API keys are forwarded without a local check; opik-backend's auth filter is
  the one place they are enforced. OAuth tokens are introspected because the
  MCP authorization spec requires the resource server to answer 401 for an
  expired token, and that 401 is the host's only signal to refresh. The
  reasoning is in the docstrings of `tests/test_http_auth.py` and
  `tests/test_oauth_token_validation.py`. No ADR covers hosted auth yet
  (OPIK_8497).
- An expired OAuth token gets HTTP 401 `invalid_token`. Before this, a dead
  token came back as a tool error inside HTTP 200, the host never refreshed,
  and users lost the connector about an hour after connecting (OPIK-8252,
  #182).
- Introspection fails open. Only a definite 401 from the backend rejects a
  request, so a backend outage does not log out every connected host
  (`IntrospectionStatus` in `src/opik_mcp/oauth_identity.py`, #182).
- Only valid answers are cached, and the cache is capped at the token's
  expiry. A refreshed token works on first use, and a revoked token is
  forwarded for at most the TTL before the backend's 401 drops the entry
  (#182).
- Credentials are bound again on every `tools/call`, because the session task
  keeps the handshake's values and would forward a token that has since been
  refreshed (#182).
- The introspected workspace name is display only, kept in its own ContextVar
  so it cannot reach the outbound `Comet-Workspace` header (#151).
- Discovery and OAuth-flow paths are proxied, not redirected, because some
  host SDKs do not follow cross-origin redirects during OAuth discovery
  (docstring of `_proxy_to_as`, #139).
- The metadata URL in the challenge sits at the host root, because the route
  is registered there and a URL under the resource path falls into the auth
  middleware and gets 401 (#139).
- Dev-token mode was removed; passthrough is the only HTTP auth mode (#139).
- The RFC 8707 audience check logs and does not reject yet: the AS and
  opik-mcp are configured separately, and a strict check could lock every
  host out in one deploy (#182).
- The server binds loopback unless `OPIK_MCP_HOST` says otherwise
  (`Settings.opik_mcp_host`). DNS-rebinding protection is on by default with
  loopback allow-lists, and a public deployment adds its own host (#147).
- Credentials are stored only as SHA-256 digests, in bounded LRU maps, so a
  long-running hosted process holds no bearer in plaintext and cannot grow
  without limit (#161).

## Proven by

- `tests/test_http_auth.py`: no bearer and non-Bearer schemes get 401; an
  API-key bearer initializes; `initialize` names the OAuth workspace end to
  end.
- `tests/test_oauth_token_validation.py`: expired token gets 401 with the
  `invalid_token` challenge; introspection failure fails open; API keys are
  never introspected; the handshake validates once; 401 and 403 tool-error
  wording per bearer kind; the TTL cache, its eviction on a backend 401 and
  its cap at `expires_at`.
- `tests/test_oauth_passthrough_mode.py`: the middleware sets and resets the
  ContextVars, captures `Comet-Workspace`, stores identity, answers a dead
  token on a tool call with 401 without forwarding, and pairs the minted
  session id with the credential.
- `tests/test_oauth_token_rotation.py`: a refreshed token is the one
  forwarded to the backend; rebinding does nothing without an HTTP request and
  resets afterwards.
- `tests/test_oauth_identity.py`: the three-way introspection outcome, blank
  fields to `None`, URL derivation, and the WARNING log on fail-open.
- `tests/test_oauth_protected_resource.py`: the metadata document, 503 with no
  AS, and no auth needed.
- `tests/test_oauth_redirect.py`: the AS proxy keeps body and query string,
  503 with no AS, proxy and path-prefixed well-known paths skip auth, JSON 404.
- `tests/test_resource_metadata_url.py`: the advertised metadata URL is at the
  host root and falls back to the relative path.
- `tests/test_http_path_config.py`: `OPIK_MCP_HTTP_PATH` default, override and
  leading-slash check; host and origin allow-lists and the rebinding toggle.
- `tests/test_health.py`: liveness without auth; readiness on 2xx, 4xx, 5xx,
  timeout, connect error, and a config error that is not swallowed.
- `tests/test_credential_identity.py`: digest keys, LRU bounds, session
  pairing stored hashed.
- `tests/test_account_identity.py`: API-key identity never blocks, never runs
  off cloud, never writes the key to disk, and does not retry on every event.

## Log

- 2026-09-23: the introspected workspace name also feeds UI links; no link when an OAuth caller's workspace is unknown (#201).
- 2026-09-04: expired OAuth tokens get 401 `invalid_token`, with a validation cache, so hosts refresh (#182).
- 2026-08-26: hosted identity failures reported as misses instead of anonymous, so they can be counted (#169).
- 2026-08-21: the MCP session id is paired with the credential, so hosted events group by session (#166).
- 2026-08-13: caller identity resolved for OAuth tokens and API keys, so events name the user (#161).
- 2026-06-25: the per-session instructions name the OAuth workspace, which no header carries (#151).
- 2026-06-18: OAuth detection matches the backend's token prefix, so real OAuth tokens take the OAuth path (#149).
- 2026-06-08: auth rejection events and the outer rejection middleware, so rejected requests are counted (#148).
- 2026-06-04: configurable transport path and Host and Origin allow-lists, for the hosted deployment (#143, #147).
- 2026-06-03: OAuth passthrough, RFC 9728 metadata and the AS proxy; dev-token mode removed, for hosted OAuth (#139).
- 2026-05-25: `/health` and `/health/ready` added, for the hosted image's probes (#122).
