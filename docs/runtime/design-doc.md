# runtime

## Purpose

Runtime is how the `opik-mcp` process starts, where its settings come from and
how a tool call reaches the Opik REST API. It answers: which credential and
workspace does a call use, what does a backend error or timeout turn into, when
does the process refuse to start, and why does no tool take a workspace?

## What it does now

### Start

- `main()` in `src/opik_mcp/__main__.py` serves stdio by default. Any other
  `OPIK_MCP_TRANSPORT` serves Streamable HTTP through uvicorn on
  `OPIK_MCP_HOST` (default loopback), with the access log off because OAuth
  query strings can carry tokens.
- The process refuses to start when a setting fails validation, when HTTP has
  `OPIK_MCP_AS_URL` but no `OPIK_MCP_RESOURCE_URI`, or when the HTTP port
  cannot be bound (`_preflight_bind_check`).

### Credential and workspace

`resolve_opik_config()` in `src/opik_mcp/opik_client.py` returns
`(base_url, api_key, workspace)` for every call:

- Credential: the inbound `Authorization` header, else `OPIK_API_KEY`, else
  none. A self-hosted backend with auth disabled works without a key.
- Workspace for an OAuth bearer: only the inbound `Comet-Workspace` header,
  which may be absent. The backend takes the workspace from the token.
- Workspace otherwise: the inbound `Comet-Workspace` header, else
  `OPIK_WORKSPACE` (alias `COMET_WORKSPACE`, which loses when both are set),
  else `default`.
- A workspace that looks like an unfilled placeholder (`looks_unsubstituted`)
  raises `MissingConfigError` before any request, with a message that names
  the setting or header and the value.
- Base URL: `OPIK_URL`, else `COMET_URL_OVERRIDE` plus `/opik/api`
  (`opik_rest_base`). An empty result raises `MissingConfigError`.

`OpikClient._headers` sends the credential verbatim as `Authorization`: an
inbound header (OAuth or API key) as the host sent it, usually
`Bearer <token>`, and `OPIK_API_KEY` raw, with no `Bearer`. A missing
credential sends no `Authorization`. A missing workspace (an OAuth bearer and
no inbound header) sends no `Comet-Workspace`.

Open question: a hosted request with an API key and no `Comet-Workspace` falls
back to the process's `OPIK_WORKSPACE`. `.claude/rules/security.md` forbids an
environment fallback when the caller supplied a value; does a missing header count?

### Errors

Reads and lists map a non-2xx answer to a typed error in `_raise_for_status`,
with an entity hint and an excerpt of the backend's message.

| Status | Error | `error_kind` |
|---|---|---|
| 401 | `OpikAuthError` | `auth` |
| 403 | `OpikPermissionError` (subclass of `OpikAuthError`) | `permission` |
| 404 | `OpikNotFoundError` | `not_found` |
| 400, 422 | `OpikValidationError` | `validation` |
| 5xx, other | `OpikServerError` | `upstream_5xx` |

A 2xx other than 200, or a body that is not a JSON object, is also
`OpikServerError`. Writes get the raw response from `OpikClient.write_json`,
which never raises on status; [writes](../writes/design-doc.md) builds the
error.

Transport errors stay `httpx` exceptions in the client. `list` turns a timeout
into a tool error that says how to narrow the call, and any other
`httpx.HTTPError` into "Could not reach Opik" (`_as_tool_error`). `read`
catches only the typed errors around its main fetch. The optional blocks of a
composite read also catch `httpx.HTTPError` and give up after
`DEADLINE_SECONDS` (`src/opik_mcp/read_list/decorations.py`).

### Timeouts and connections

- Each backend request has the client timeout `_DEFAULT_TIMEOUT`. A `list`
  call with free-text `search` uses `_SEARCH_TIMEOUT_S`, since a cold backend
  search was measured to outlast the default (#185).
- A read or list opens one `httpx.AsyncClient` for the whole call
  (`client_for_call`) and closes it when the call ends, also on error. A client
  passed in by the caller is never closed.
- A write builds its client with `make_opik_client()` and no shared
  connection, so each request opens its own. A dry run builds no client.

## How it works

```
read/list -> client_for_call -> make_opik_client -> resolve_opik_config
          -> OpikClient.get_* / list_* -> _get_json | _post_json -> _raise_for_status
write     -> writes/dispatch.py -> make_opik_client -> OpikClient.write_json
```

- To add a read endpoint, add an `OpikClient` method on `_get_json` (as
  `get_project` does for `read('project')`) or on `_post_json` for a POST read.
  Add it to the `OpikListClient` or `OpikReadClient` protocol so test fakes see
  it, and test it in `tests/client/test_read.py`.
- To change where the credential or workspace comes from, start in
  `resolve_opik_config`. For startup and its refusals, `_run_transport`.

- `opik_client.py` knows endpoints and wire formats. Answer shaping, project
  names and UI links live in the entity namespaces ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)).
- Bearer kinds, the middleware and the 401 cache drop (`note_backend_401`):
  [hosted-auth](../hosted-auth/design-doc.md).
- Startup events and the lifecycle sentinel: [analytics](../analytics/design-doc.md).
- The tools and composite-read blocks that call the client:
  [tool-surface](../tool-surface/design-doc.md).

## Decisions

- No tool takes a workspace; it is bound when the client is built. The
  workspace belongs to the credential and the session
  (`.claude/rules/security.md`), and a field on every tool costs surface bytes
  ([ADR 0001](../decisions/0001-context-budget-first.md)).
- An unset workspace falls back to `default`, as the Opik SDK does, so a local
  install runs without one. On cloud the backend reads it as the account's
  default workspace, which may not be the one meant (comment on
  `DEFAULT_WORKSPACE`) (#141).
- The placeholder detector stays narrow because workspace names have no
  charset limit and a false positive breaks a working install (#162).
- One connection per read or list call, since a composite read paid a
  handshake per backend request (measured in the `client_for_call`
  docstring). A process-wide client was left out so no client outlives its
  call (#187).
- Writes share `write_json` with templated paths, so a new write operation
  needs no client change ([ADR 0003](../decisions/0003-five-tool-surface.md)).

### Traps

- The settings field is `Settings.comet_workspace`. `OPIK_WORKSPACE` reaches
  it through `AliasChoices`, so grepping `opik_workspace` finds no field.
- `get_settings()` is `lru_cache`d for the life of the process. A test that
  changes the environment must call `get_settings.cache_clear()`.
- `list_traces`, `list_spans`, `list_threads` and `get_thread` raise a plain
  `ValueError` without a project id or name, before any request. The tool
  layers do not translate it, so resolve the project first.
- Wire quirks are handled once each: `_search_params` (unset values are not
  sent, since an empty `filters=` is a 400), `_ids_param` (a JSON array) and
  the trailing slash in `list_automation_rules`.

## Proven by

- Credential, workspace, base URL and placeholders: `tests/client/test_client.py`.
- Settings and alias precedence: `tests/server/test_config.py`.
- Read paths, queries and the error mapping: `tests/client/test_read.py`, `tests/client/test_search.py`.
- One connection per call: `tests/client/test_connection_per_tool_call.py`.
- Timeout and unreachable messages in `list`: `tests/read_list/test_list_filters.py`.
- The decoration deadline: `test_a_slow_decoration_does_not_hold_up_the_answer`.
- Startup refusals: `tests/analytics/test_server_startup.py`, `tests/analytics/test_subprocess.py`.
- A real stdio handshake: `tests/hermetic/test_stdio_session.py`.

## Log

- 2026-09-11: one HTTP connection per read or list call, closed with the call (#187).
- 2026-09-08: `list` search takes `_SEARCH_TIMEOUT_S`, since a cold backend search can outlast the default (#185).
- 2026-08-14: an unfilled workspace placeholder fails with a message that names the setting (#162).
- 2026-06-24: `OPIK_API_KEY` made optional for self-hosted backends with auth disabled (#150).
- 2026-06-04: `OPIK_WORKSPACE` added, `COMET_WORKSPACE` kept as an alias, `default` when unset (#141).
- 2026-06-03: HTTP start refused with `OPIK_MCP_AS_URL` but no `OPIK_MCP_RESOURCE_URI` (#139).
- 2026-05-22: bind preflight before uvicorn, since uvicorn hides a taken port (#117).
