# runtime

## Purpose

Runtime covers how the `opik-mcp` process starts, where its settings come from,
and how a tool call reaches the Opik REST API. Open this doc to find out which
client method a read calls, which headers carry the credential and the
workspace, what a backend error turns into, and why no tool takes a workspace
argument.

## What it does now

### Starting the process

`python -m opik_mcp` runs `main()` in `src/opik_mcp/__main__.py`. It reads
`Settings` once through `get_settings()` in `src/opik_mcp/config.py` (cached for
the life of the process), configures logging to stderr at
`OPIK_MCP_LOG_LEVEL`, and picks a transport from `OPIK_MCP_TRANSPORT`.

- `stdio` is the default. `_run_transport` imports the `mcp` server from
  `src/opik_mcp/server.py`, installs the `tools/list` emitter and the skill
  resources, and calls `mcp.run(transport="stdio")`. There is no port and no
  inbound auth: whoever spawned the process owns its stdin and stdout.
- Any other value serves Streamable HTTP through uvicorn at `OPIK_MCP_HOST`
  and `OPIK_MCP_PORT`. The host defaults to `127.0.0.1`, so a local server
  listens on loopback only; binding elsewhere means setting `OPIK_MCP_HOST`
  (`Settings.opik_mcp_host`). uvicorn runs with `access_log=False`, so query
  strings from the OAuth flow, which can carry tokens, never reach stdout
  (`_run_transport`). `OPIK_MCP_RELOAD` runs uvicorn with `reload=True` over
  `src` and the `build_app` factory.

The process refuses to start in three cases:

- A setting fails validation, for example `COMET_WORKSPACE_ID` that is not a
  UUID (`Settings._validate_workspace_uuid`) or `OPIK_MCP_HTTP_PATH` without a
  leading slash (`Settings._require_leading_slash`). `main()` reports a
  startup error and re-raises the `ValidationError`.
- HTTP transport with `OPIK_MCP_AS_URL` set and `OPIK_MCP_RESOURCE_URI` unset.
  `_run_transport` logs why and exits with status 1. The authorization server
  matches the `resource` parameter exactly against its own configured URI, so
  a guessed value would fail every authorize call.
- HTTP transport on a port it cannot bind. `_preflight_bind_check` binds and
  releases the socket first (resolving the address family through
  `getaddrinfo`, so `::1` and `localhost` work), because uvicorn logs a bind
  failure and returns normally, and the failure would otherwise go unseen.

The startup and shutdown events these paths emit are described in
[analytics](../analytics/design-doc.md).

### Settings

`Settings` in `src/opik_mcp/config.py` is a pydantic-settings model read from
the environment, case-insensitive, with unknown variables ignored. The fields
runtime uses:

- `OPIK_API_KEY`: optional. Self-hosted backends run with auth disabled and
  authorize on the workspace header alone.
- `OPIK_WORKSPACE`, with `COMET_WORKSPACE` as a deprecated alias; when both are
  set, `OPIK_WORKSPACE` wins. Left `None` when unset; the `default` fallback is
  applied where the client is built, so analytics can still tell whether the
  user set one.
- `OPIK_URL`, else `COMET_URL_OVERRIDE` (default `https://www.comet.com`) plus
  `/opik/api`. `opik_rest_base()` in `src/opik_mcp/opik_client.py` holds that
  rule; the OAuth introspection in `src/opik_mcp/oauth_identity.py` uses the
  same function.
- `OPIK_DEFAULT_PROJECT_NAME`: a hint rendered into the `initialize`
  instructions. Tools stay stateless and the agent passes a project on each
  call (see [tool-surface](../tool-surface/design-doc.md)).
- `OPIK_MCP_TRANSPORT`, `OPIK_MCP_HOST`, `OPIK_MCP_PORT`, `OPIK_MCP_RELOAD`,
  `OPIK_MCP_LOG_LEVEL`.

The OAuth, HTTP path and transport-security fields belong to
[hosted-auth](../hosted-auth/design-doc.md); the analytics and Sentry fields
belong to [analytics](../analytics/design-doc.md).

### Which credential and workspace a call uses

Every call builds its client through `resolve_opik_config()` in
`src/opik_mcp/opik_client.py`, which returns `(base_url, api_key, workspace)`:

- Credential: the inbound `Authorization` header of the HTTP request being
  served, if there is one; otherwise `OPIK_API_KEY`; otherwise none. The
  inbound value is read from the ContextVars in `src/opik_mcp/auth_context.py`,
  which the HTTP middleware sets (see
  [hosted-auth](../hosted-auth/design-doc.md)). Under stdio there is no inbound
  request, so the environment key is used.
- OAuth token: when the inbound header is `Bearer` followed by a token that
  starts with `OAUTH_ACCESS_TOKEN_PREFIX`, the backend derives the workspace
  from the token. The workspace is then only what the inbound `Comet-Workspace`
  header carried, and may be `None`. The check is a prefix match, so an API
  key that contains the marker in the middle does not skip the workspace.
- Anything else: the inbound `Comet-Workspace` header, else `OPIK_WORKSPACE`,
  else `default`. `default` mirrors the Opik SDK and is right for a local
  install with one workspace. On cloud the backend reads it as the account's
  default workspace, which may not be the one the user meant (comment on
  `DEFAULT_WORKSPACE`).
- A workspace that looks like an unfilled config placeholder (`${…}`, `<…>`,
  `{{…}}`, `%…%`, or a bare `$UPPER_CASE` or `$(UPPER_CASE)`) raises
  `MissingConfigError` before any request goes out. The message names the
  setting or the header, shows the value and says what to put there
  (`unfilled_workspace_error`). The match is narrow on purpose: workspace names
  have no charset limit, so `$acme` passes (`looks_unsubstituted`).
- An empty base URL raises `MissingConfigError`, so a request never goes to a
  relative `/opik/api`.

`MissingConfigError` carries `error_kind = "validation"`, so it is counted as a
setup problem the user can fix.

Unverified: whether a hosted request that sends an API key and no
`Comet-Workspace` header should use the process's `OPIK_WORKSPACE`. The code
does; `.claude/rules/security.md` says a request uses its caller's workspace
only.

### Why tools take no workspace argument

The workspace is bound when `OpikClient` is constructed and sent on every
request (`OpikClient._headers`). No tool on the surface takes a workspace. The
workspace belongs to the credential and the session: the environment under
stdio, the inbound request under HTTP, the token row for OAuth. A tool argument
would let the model point a call at a workspace the session was not set up for,
which `.claude/rules/security.md` forbids, and every tool schema would carry
the field in the host's context on every request
([ADR 0001](../decisions/0001-context-budget-first.md)).

### Headers on every request

`OpikClient._headers()` sends:

- `Content-Type` and `Accept`: `application/json`.
- `Authorization`: the credential exactly as resolved (an API key, or the
  inbound `Bearer` value). It is omitted when there is none.
- `Comet-Workspace`: the resolved workspace. It is omitted when there is none,
  which happens only for an OAuth token.
- `Idempotency-Key`: on writes only, when the dispatcher passes one
  (`OpikClient.write_json`).

### How a read reaches the backend

Take `read('trace', id)`. `run_read` in `src/opik_mcp/read_list/read_tool.py`
opens `client_for_call()`, which creates one `httpx.AsyncClient` and wraps it in
an `OpikClient` from `make_opik_client()`. The entity's fetcher then calls
`OpikClient.get_trace`, which sends `GET {base}/v1/private/traces/{id}`
through `_get_json`. The spans are a second call, `list_spans`, on the same
connection. `list` goes through `client_for_call()` the same way in
`src/opik_mcp/read_list/list_tool.py`.

Each read method on `OpikClient` maps onto one REST endpoint under
`/v1/private/`. Most are `GET` through `_get_json`. Endpoints that the backend
models as a POST with a body (the KPI cards, a project metric, a thread
retrieve) go through `_post_json`. Both expect exactly 200 and a JSON object.
The protocols `OpikListClient` and `OpikReadClient` list the methods the read
and list registry depends on, so test fakes need not subclass the client.

Query parameters are sent only when set. The backend treats an empty
`filters=` as malformed JSON and answers 400 (`_search_params`). A few wire
details are handled once in the client:

- `truncate` goes out as the lowercase literal `true` or `false`.
- On the experiment comparison endpoints, `experiment_ids` is a JSON array, not a comma-joined list (`_ids_param`).
- A project's score names take the project id as a JSON array
  (`list_project_score_names`).
- The online-rules path keeps its trailing slash, without which the backend
  answers 404 (`list_automation_rules`).
- `list_traces` and `list_spans` refuse to run without a project id or name.

The entity modules that call these methods are described in
[tool-surface](../tool-surface/design-doc.md) and the feature docs.

### How a write reaches the backend

Writes do not use per-endpoint methods. `dispatch` in
`src/opik_mcp/writes/dispatch.py` builds a path from the operation's template
and a body, then calls `OpikClient.write_json(method, path, body,
idempotency_key=…)`. `write_json` serialises the body with no fallback encoder,
so a value that is not plain JSON fails here instead of reaching the backend in
the wrong shape. It returns the raw response without raising on 4xx or 5xx;
the dispatcher turns those into its own error envelope (see
[writes](../writes/design-doc.md)). A dry run builds no client at all.

`OpikClient` still has per-endpoint score and comment methods
(`add_trace_feedback_score`, `add_trace_comment` and their span and thread
versions). Only `tests/test_opik_client.py` calls them.

### Connections

A read or list call owns one `httpx.AsyncClient` for the length of the call
(`client_for_call`). A composite read (a trace and its spans, a project and its
decorations) reuses one connection instead of paying a TCP and TLS handshake
per backend request. The client is closed when the call ends, including on an
error, so nothing carries into the next call. A client passed in by the caller
is used as it is and never closed.

A write builds its client with `make_opik_client()` and no shared connection,
so each backend request in a write opens its own `httpx.AsyncClient`
(`OpikClient._http`, `dispatch`).

### Timeouts

The client's default timeout is 30 seconds per request (`_DEFAULT_TIMEOUT`).
`list` passes 60 seconds when the call has a free-text `search`, because the
backend's search can take over 30 seconds on a cold cache (`_SEARCH_TIMEOUT_S`
in `src/opik_mcp/read_list/list_tool.py`). The code carries no host-side
timeouts.

### Backend errors

`_raise_for_status` maps every non-2xx answer on the read path to a typed
error. The message includes an entity hint (for example `trace 'abc'`) and up
to 200 characters of the backend's `message`, `errors` or `error` field
(`_error_detail`):

| Status | Error | `error_kind` |
|---|---|---|
| 401 | `OpikAuthError` | `auth` |
| 403 | `OpikPermissionError` (a subclass of `OpikAuthError`) | `permission` |
| 404 | `OpikNotFoundError` | `not_found` |
| 400, 422 | `OpikValidationError` | `validation` |
| 5xx, other | `OpikServerError` | `upstream_5xx` |

Each class carries `error_kind` and `http_status` as ClassVars, which
[analytics](../analytics/design-doc.md) reads. A 200 with a non-JSON body, or
with JSON that is not an object, is also an `OpikServerError`.

On a 401, `note_backend_401()` checks the inbound bearer. For an OAuth token it
drops the cached validation, so the next MCP request gets the `invalid_token`
401 that makes the host refresh, and the message says the token expired. For an
API key the message says to check `OPIK_API_KEY` and `OPIK_WORKSPACE`. The
write dispatcher calls the same function on its 401s.

Transport errors (timeouts, refused connections) are not translated by the
client and propagate as `httpx` exceptions. `list` turns a timeout into a tool
error that says how to narrow the query, and any other `httpx.HTTPError` into
"Could not reach Opik" (`_as_tool_error`). `read` turns the typed errors into
a status-specific message (`_format_client_error`) and does not catch `httpx`
errors.

### Wiring in server.py

Importing `src/opik_mcp/server.py` builds the `FastMCP` instance with the
instructions rendered once, and registers the tools. The two transports then
add different things:

- stdio (`_run_transport`): the `tools/list` emitter and the skill resources.
- HTTP (`build_app`): the same two, plus per-session instructions and
  per-request auth rebinding (see
  [hosted-auth](../hosted-auth/design-doc.md)), then the Starlette app.

`build_app` replaces the app's lifespan with `_make_composed_lifespan`, which
wraps FastMCP's own lifespan. The inner lifespan starts the streamable-HTTP
session manager, without which every MCP request hangs. The wrapper emits
start and shutdown events only when `main()` has not claimed them: `main()`
sets an environment sentinel (`boot_props.mark_lifecycle_owned_by_main`) before
`build_app` can run, so a boot is counted once whether the process started
from `main()` or from `uvicorn … --factory`.

## How it works

Call path for a read or list:

`server.py` tool → `read_list/read_tool.py` `run_read` or
`read_list/list_tool.py` `run_list` → `opik_client.client_for_call` →
`make_opik_client` → `resolve_opik_config` → `OpikClient(base_url, api_key,
workspace, client=…)` → an entity fetcher calls `OpikClient.get_*` or
`list_*` → `_get_json` or `_post_json` → `_raise_for_status`.

Call path for a write:

`writes/dispatch.py` `dispatch` → `make_opik_client` → operation hooks →
`OpikClient.write_json` → raw `httpx.Response` back to the dispatcher.

Modules this doc owns:

- `src/opik_mcp/__main__.py`: `main`, `_run_transport`,
  `_preflight_bind_check`, the OAuth resource-URI guard. The event emitters in
  it are [analytics](../analytics/design-doc.md).
- `src/opik_mcp/config.py`: `Settings` except its OAuth, HTTP and analytics
  fields; `get_settings`, `DEFAULT_WORKSPACE`, `looks_unsubstituted`,
  `MissingConfigError`, `unfilled_workspace_error`, `installation_type`.
- `src/opik_mcp/opik_client.py`: the whole module.
- `src/opik_mcp/server.py`: the `mcp` instance, `_make_composed_lifespan`, and
  the order of the `install_*` calls. The tool registrations are
  [tool-surface](../tool-surface/design-doc.md),
  [writes](../writes/design-doc.md) and [skills](../skills/design-doc.md); the
  middleware and routes are [hosted-auth](../hosted-auth/design-doc.md).

Boundaries: `opik_client.py` knows endpoints and wire formats, not entities or
operations. It does not shape answers, resolve project names or build UI
links. Those live in the entity and operation namespaces
([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)).

## Decisions

- Workspace is bound at client construction and sent in `Comet-Workspace`; no
  tool takes a workspace argument. Reason: the workspace belongs to the
  credential and the session (`.claude/rules/security.md`), and a field on
  every tool costs surface bytes
  ([ADR 0001](../decisions/0001-context-budget-first.md)).
- One HTTP connection per read or list call, closed at the end of the call.
  Measured against the cloud backend, each extra request with its own client
  cost about 259 ms. A process-wide client would also save the handshake
  between calls, but was left out so that no client outlives the request that
  made it (`client_for_call` docstring, #187).
- Read methods map one to one onto endpoints; writes go through one generic
  `write_json` with templated paths, so a new write operation needs no client
  change ([ADR 0003](../decisions/0003-five-tool-surface.md),
  [ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)).
- `OPIK_API_KEY` is optional, so a self-hosted backend with auth disabled works
  on the workspace header alone.
- An unset workspace falls back to `default`, as the Opik SDK does, so a local
  install runs without one (#141).
- An unfilled workspace placeholder fails before the request with a message
  that names the setting, instead of an upstream auth error that names
  neither. The detector stays narrow because a false positive breaks a working
  install (#162).
- The process refuses to start on a bind failure or on OAuth without a
  resource URI, because uvicorn hides bind failures and a missing resource URI
  fails every authorize call.
- `main()` owns the lifecycle events and `build_app` defers to it through an
  environment sentinel, so a boot is counted once (#148).
- `list` gets a longer timeout only for free-text search, the one call measured
  to exceed 30 seconds (#185).
- Not built: splitting `opik_client.py` into smaller modules is filed as
  OPIK_8496.

## Proven by

- `tests/test_opik_client_read.py`: each read method's path, query and body;
  status-to-error mapping on reads (`test_get_maps_status_to_typed_error`);
  non-JSON and non-object bodies become server errors.
- `tests/test_opik_client_search.py`: filter, sort, search and window
  parameters go out only when set; spans list project-wide without a trace id.
- `tests/test_opik_client.py`: `resolve_opik_config` base URL, optional key,
  `default` workspace, OAuth prefix detection, the OAuth client omitting
  `Comet-Workspace`, the placeholder detector's hits and misses; a keyless
  request against an authenticated backend surfaces a 401
  (`test_no_api_key_against_authenticated_backend_surfaces_401`); an injected
  `httpx` client is used.
- `tests/test_config.py`: `OPIK_WORKSPACE` wins over `COMET_WORKSPACE`;
  defaults and environment loading.
- `tests/test_connection_per_tool_call.py`: a composite read and a list each
  build one client, an injected client is left open, the connection does not
  outlive the call.
- `tests/test_analytics_server_startup.py`: the OAuth resource-URI guard
  (`test_startup_error_when_oauth_enabled_without_resource_uri`,
  `test_no_startup_error_when_resource_uri_set`); `main()` claims the
  lifecycle before `build_app` and turns off the access log
  (`test_http_main_owns_lifecycle_and_disables_access_log`,
  `test_reload_http_disables_access_log`); invalid settings stop the start
  (`test_startup_error_on_invalid_workspace_uuid`).
- `tests/test_analytics_subprocess.py`: the bind preflight in a real process
  (`test_preflight_handles_ipv6_loopback_via_getaddrinfo`,
  `test_port_in_use_emits_transport_crash_in_subprocess`).
- `tests/test_analytics_lifespan.py`: the composed lifespan emits only when
  `main()` does not own the lifecycle
  (`test_lifespan_skips_emit_when_owned_by_main`).
- `tests/e2e/test_stdio_session.py`: the stdio process starts and completes a
  handshake (`test_the_server_starts_and_completes_a_handshake`).

## Log

- 2026-09-11: one HTTP connection per read or list call (`client_for_call`) (#187).
- 2026-09-08: `list` search takes a 60 s timeout; searchable list endpoints forward filter, sort, search and window (#185).
- 2026-09-04: a backend 401 on an OAuth token drops its cached validation so the host refreshes (#182).
- 2026-09-03: `ask_ollie` and `run_experiment` removed, with the settings only they used (#181).
- 2026-08-14: an unfilled workspace placeholder fails with a message that names the setting (#162).
- 2026-06-24: `OPIK_API_KEY` made optional for self-hosted backends.
- 2026-06-08: `build_app` lifespan emits lifecycle events unless `main()` owns them (#148).
- 2026-06-04: `OPIK_WORKSPACE` added, `COMET_WORKSPACE` kept as an alias, workspace optional with `default` (#141).
- 2026-06-03: HTTP start refused when `OPIK_MCP_AS_URL` is set without `OPIK_MCP_RESOURCE_URI` (#139).
- 2026-05-22: bind preflight before uvicorn, so a taken port is reported.
