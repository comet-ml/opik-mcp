# analytics

## Purpose

opik-mcp sends product events to Comet's BI endpoint and stack traces of
unexpected failures to Sentry. This doc answers which events fire and what
they carry, how a failure gets its `error_kind`, what may never be sent, and
how to add an event or a property.

## What it does now

Both channels default to on. `OPIK_MCP_ANALYTICS_ENABLED=false` and
`OPIK_MCP_SENTRY_ENABLED=false` turn them off. Telemetry never fails a tool
call or hides a startup error.

### Events

Every name starts with `opik_mcp_` (`src/opik_mcp/analytics/events.py`).

| Event | Fires when | Emitted by |
|---|---|---|
| `server_started` | the process starts serving | `main()`, or the `build_app()` lifespan when `main()` did not run |
| `startup_error` | settings fail validation, HTTP OAuth config is incomplete, or the transport crashes | `_emit_startup_error` in `src/opik_mcp/__main__.py` |
| `tools_listed` | the first `tools/list` of a session | `install_tools_listed_emitter` in `src/opik_mcp/analytics/wrappers.py` |
| `session_initialized` | the first tool call of a session | `instrument_tool` in `src/opik_mcp/analytics/wrappers.py` |
| `tool_called` | a call to `read`, `list`, `write`, `schema` or `read_skill` that passed argument validation | `instrument_tool` |
| `auth_rejected` | HTTP only: 401, 403 or 421 on an authenticated path | `AuthRejectionMiddleware` in `src/opik_mcp/server.py` |
| `server_shutdown` | the process stops, with a `reason` | `_emit_server_shutdown` in `src/opik_mcp/__main__.py`, or the lifespan |

`initialize` produces no event. `tools_listed` and `session_initialized` are
sent once per MCP session, the `ServerSession` a connection opens. A stdio
process serves one connection, so there it means once per process.

### What an event carries

- The body is `{"user_id", "event_type", "event_properties"}`. Properties
  merge per-request fields, the caller's properties, then the common block,
  which wins (`AnalyticsClient._build_event` in
  `src/opik_mcp/analytics/client.py`). An inbound `Comet-Workspace` header
  outranks the workspace setting.
- `user_id` is the caller's Comet login in plaintext when it resolves
  (`user_id_kind=comet_user`), else the install id. The login appears in no
  other field. `identity_lookup` separates `none_expected` (no credential)
  from `miss` (a credential that did not resolve).
- `tool_called` adds `tool_name`, `success`, `duration_ms` and the per-call
  host and environment context. A failure adds `error_kind`,
  `exception_type`, `cause_type` and `http_status`. The tool's own props
  are added on success only.
- Every property is a boolean string, an allowlisted value, a bucket, an
  integer as a string (`duration_ms`, `http_status`, list `page` and `size`),
  an exception class name or a SHA-256 digest. The exceptions are the
  workspace fields and `user_id`.
  Free text, paths, argument values, filter values, exception messages and
  raw tokens are never sent.

### Error kinds

`error_kind` is a value of `ErrorKind` in `src/opik_mcp/error_kinds.py`.
`bucket_exception` in `src/opik_mcp/analytics/errors.py` decides it:

1. A bare `ToolError` is unwrapped to its cause. `exception_type` keeps the
   wrapper class, `cause_type` the leaf.
2. A status on the instance (`httpx.HTTPStatusError`, `BackendError`) goes
   through `bucket_http_status`.
3. The class's `error_kind: ClassVar[ErrorKind]`.
4. Pydantic and httpx classes, then `unknown`.

Host cancellation is `cancelled`. The classifier never reads `str(exc)` or
`exc.args`. `invalid_config` is used only by the startup error.

`instrument_tool` sends a failure to Sentry unless its kind is in
`_USER_SIDE_ERROR_KINDS` or the cause is a `MissingConfigError`. Sentry sends
no PII, and `before_send` caps events per process at `_MAX_EVENTS`
(`src/opik_mcp/error_tracking.py`).

## How it works

```
tool handler wrapped by instrument_tool
  -> _maybe_emit_session_initialized
  -> the tool; on failure bucket_exception, maybe _report_to_sentry
  -> track_event(EVENT_TOOL_CALLED) -> _build_event -> bounded queue
  -> daemon thread POSTs, retries, drops when the queue is full
```

To change something, start here:

- A tool's props (a new `read` prop such as `has_fields`): `_read_props` and its
  siblings in `src/opik_mcp/server.py`, computed from the call's arguments.
  Only `_list_props` adds page facts, from tool-surface's `page_facts`.
- The common block or identity: `src/opik_mcp/analytics/client.py`.
- Startup and shutdown emits: `src/opik_mcp/__main__.py`.
- Real payloads: run `scripts/capture_bi_listener.py` and point
  `OPIK_MCP_ANALYTICS_URL` at it.

To add a property:

1. Return it from the props function, bucketed or allowlisted.
2. For an enum, add a `Literal` in `src/opik_mcp/analytics/events.py` and a
   test in `tests/analytics/test_events.py` that it matches the classifier.
3. Assert its output and a canary in `tests/analytics/test_privacy.py`, as
   `test_read_props_buckets_id_kind_without_leaking` does for `_read_props`.
4. A new value is a BI schema change (the docstring in `src/opik_mcp/analytics/events.py`). Unverified: whether a new property counts too; say so in the PR for BI to be safe.

Boundaries:

- Exception classes, and whether `main()` or `build_app()` emits lifecycle events: [runtime](../runtime/design-doc.md), with `WriteError` in [writes](../writes/design-doc.md).
- The image entrypoint: [release](../release/design-doc.md).
- Middleware placement and identity: [hosted-auth](../hosted-auth/design-doc.md).
- `page_facts`, `filter_field_names`, `sort_field_label`: [tool-surface](../tool-surface/design-doc.md).

## Decisions

No ADR covers analytics. The reasons come from PRs.

- `error_kind` is a `ClassVar` so a new exception declares its own bucket.
  `BackendError` uses its instance status, since it wraps many (#128, #130).
- A bare `ToolError` is unwrapped before bucketing. Before that, every tool
  failure showed as `unknown` (#127).
- Classifications that dashboards use are frozen (`parent_process`,
  `mcp_host`, the POSIX `launch_method` table). Corrections go into new
  fields (`host_process`, `launcher`, `mcp_client`) so no series moves (#165).
- `user_id` is the plaintext login because the rest of the product sends it
  and it is the warehouse's user key; a digest could not be joined (#161).
- Both switches are set to false in `tests/conftest.py` (with `setdefault`)
  and in the CI workflow env. The in-process guard, `_in_pytest` in
  `src/opik_mcp/error_tracking.py`, covers Sentry only and misses a
  subprocess started with a clean environment (#175).

### Traps

- A call whose arguments fail FastMCP's schema check never reaches
  `instrument_tool`, so it sends no `tool_called` and no
  `session_initialized`. Failure rates count only failures inside a tool.
- `setup_sentry` is called only from `main()`. A process served through
  `build_app()` alone has no Sentry. The Sentry user comes from process
  settings, so on the hosted server it is never the caller.
- `tests/analytics/test_privacy.py` records at `track_event` and cannot see
  the common block; `tests/analytics/test_client_build_event.py` covers it.

## Proven by

- Wire shape, opt-out, queue, retry, and the common block:
  `tests/analytics/test_client.py`, `tests/analytics/test_client_build_event.py`.
- `tool_called`, `session_initialized` and the Sentry skip list:
  `tests/analytics/test_wrappers.py`, `tests/analytics/test_tools_listed.py`.
- Error bucketing and the fingerprint: `tests/analytics/test_errors.py`,
  `tests/analytics/test_environment.py`.
- Startup, shutdown and lifecycle ownership: `tests/analytics/test_lifespan.py`,
  `tests/analytics/test_server_startup.py`, `tests/analytics/test_subprocess.py`.
- No canary string or raw token reaches an event or Sentry:
  `tests/analytics/test_privacy.py`, `tests/analytics/test_auth_rejected.py`.
- Sentry setup, scope and cap: `tests/server/test_error_tracking.py`.
- Telemetry is off for the test process: `tests/repo/test_telemetry_disabled_in_tests.py`.

## Log

- 2026-09-21: list events carry `empty` and `source_defaulted`, so empty answers can be counted (#192).
- 2026-08-25: `install_id_kind` and `identity_lookup`, so hosted identity failures can be counted (#169).
- 2026-05-25: Sentry error tracking, for failures that need a stack trace (#125).
