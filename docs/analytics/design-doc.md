# analytics

## Purpose

opik-mcp sends two kinds of telemetry: product events to Comet's BI endpoint,
and stack traces of unexpected failures to Sentry. This doc lists which events
fire and when, what each one carries, how errors are sorted into kinds, and
how both channels stay off in tests. Read it before you add an event or a
property, or when you need to explain a number on a dashboard.

## What it does now

### Channels and switches

Product events go by HTTP POST to `OPIK_MCP_ANALYTICS_URL`, by default
`https://stats.comet.com/notify/event/` (`Settings` in `src/opik_mcp/config.py`).
`OPIK_MCP_ANALYTICS_ENABLED=false` turns them off: `track_event` returns at
once and no worker thread starts (`AnalyticsClient` in
`src/opik_mcp/analytics/client.py`; `test_disabled_skips_post`). Sentry has its
own switch, `OPIK_MCP_SENTRY_ENABLED` (`setup_sentry` in
`src/opik_mcp/error_tracking.py`; `test_setup_sentry_returns_false_when_disabled`).
Its DSN is a `ClassVar` and cannot be set from the environment
(`test_settings_dsn_is_not_env_overridable`). Both switches default to on
(`test_the_defaults_are_still_on_so_the_guard_is_doing_work`).

### Events

Every event name starts with `opik_mcp_` (`src/opik_mcp/analytics/events.py`;
`test_event_constants_exist`).

| Event | Fires when | Emitted by |
|---|---|---|
| `opik_mcp_server_started` | the process starts serving | `main()` in `src/opik_mcp/__main__.py`, or the `build_app()` lifespan |
| `opik_mcp_startup_error` | settings fail validation, HTTP OAuth config is incomplete, or the transport crashes | `_emit_startup_error` in `src/opik_mcp/__main__.py` |
| `opik_mcp_tools_listed` | the first `tools/list` of a session | `install_tools_listed_emitter` in `src/opik_mcp/analytics/wrappers.py` |
| `opik_mcp_session_initialized` | the first tool call of a session | `instrument_tool` in `src/opik_mcp/analytics/wrappers.py` |
| `opik_mcp_tool_called` | every call to `read`, `list`, `write`, `schema`, `read_skill` | `instrument_tool` |
| `opik_mcp_auth_rejected` | HTTP only: 401, 403 or 421 on an authenticated path | `AuthRejectionMiddleware` in `src/opik_mcp/server.py` |
| `opik_mcp_server_shutdown` | the process stops, for any reason | `_emit_server_shutdown` in `src/opik_mcp/__main__.py`, or the `build_app()` lifespan |

### From process start to the first tool call, in stdio mode

`main()` in `src/opik_mcp/__main__.py` runs these steps in order:

1. `get_settings()`. If it raises a pydantic `ValidationError`, a fallback
   client built with `Settings.model_construct()` sends
   `opik_mcp_startup_error` (`phase=config`, `error_kind=invalid_config`) and
   the error is re-raised. The fallback reads the opt-out, URL and source from
   the environment by hand, so opt-out still holds
   (`_build_fallback_analytics_client`;
   `test_startup_error_emits_via_fallback_when_settings_validation_fails`,
   `test_opt_out_suppresses_emit_in_subprocess`).
2. `error_tracking.setup_sentry(settings)`.
3. `boot_props.mark_lifecycle_owned_by_main()` sets the environment variable
   `_OPIK_MCP_LIFECYCLE_OWNED_BY_MAIN`.
4. `collect_environment_fingerprint()`, before the uptime clock starts, so its
   subprocess calls do not count toward the lifespan bucket.
5. `opik_mcp_server_started` with `lifecycle_source=main`
   (`test_main_emits_server_started_then_runs`).
6. `_run_transport` installs the `tools/list` emitter and starts stdio.

The host's `initialize` produces no event. The first `tools/list` of the
session sends `opik_mcp_tools_listed`. The first tool call sends
`opik_mcp_session_initialized`, and `opik_mcp_tool_called` follows when the
call ends. Both are sent once per session, tracked in a `WeakSet`
(`_maybe_emit_tools_listed`, `_maybe_emit_session_initialized`;
`test_tools_listed_dedups_per_session`).

When `main()` ends, `opik_mcp_server_shutdown` carries `reason`:
`clean_exit`, `sys_exit`, `keyboard_interrupt` or `transport_error`
(`tests/test_analytics_lifespan.py`). A transport crash also sends
`opik_mcp_startup_error` (`phase=transport_start`,
`error_kind=transport_crash`) and a Sentry event with transaction `startup`
(`test_transport_crash_propagates_full_context_to_sentry`). Its
`exception_type` comes from an allowlist; `OSError` subclasses report as
`OSError`, anything else unlisted as `unknown`
(`_bucket_transport_exception_type`;
`test_transport_crash_buckets_oserror_subclass_via_mro`).

### HTTP mode

Through `main()`, HTTP adds two startup errors: OAuth configured without
`OPIK_MCP_RESOURCE_URI` (`phase=config`, then exit;
`test_startup_error_when_oauth_enabled_without_resource_uri`), and a port the
bind preflight cannot take, reported as a transport crash
(`test_port_in_use_emits_transport_crash_in_subprocess`). The preflight is
[runtime](../runtime/design-doc.md)'s.

The hosted image can call `build_app()` without `main()`. Its composed
lifespan then sends `opik_mcp_server_started` and `opik_mcp_server_shutdown`
with `lifecycle_source=lifespan`, unless `main()` set the variable from step
3 (`_make_composed_lifespan` in `src/opik_mcp/server.py`;
`test_lifespan_emits_started_and_shutdown_when_not_owned_by_main`,
`test_lifespan_skips_emit_when_owned_by_main`). The flag is an environment
variable so uvicorn `--reload` workers inherit it (`LIFECYCLE_SENTINEL` in
`src/opik_mcp/analytics/boot_props.py`).

`AuthRejectionMiddleware` wraps the whole app and reads only the response
status. It skips health, discovery and OAuth proxy paths, because a 401
proxied from the authorization server is not this server's rejection
(`test_unauth_path_rejection_is_not_ours`). The event carries
`rejection_reason` (`missing_header`, `not_bearer`, `empty_token`,
`token_rejected`, `host_rejected`, `origin_rejected`), derived from the status
and the shape of the `Authorization` header, and `path_bucket` (`mcp`,
`health`, `well_known`, `other`) in place of the path (`bucket_path`;
`test_raw_token_never_in_event`). Auth itself is
[hosted-auth](../hosted-auth/design-doc.md)'s.

### What every event carries

The POST body is `{"user_id", "event_type", "event_properties"}`
(`AnalyticsClient._build_event`; `test_track_event_posts_wire_shape`).
Properties merge per-request fields, then the caller's properties, then a
common block, and the common block wins
(`test_three_tier_merge_properties_wins_over_per_request`).

The common block:

- `environment`, `opik_mcp_version`, `transport`, `python_version`,
  `platform`, a client-side `timestamp`, and `installation_type` (`cloud`,
  `self-hosted` or `local`).
- `install_id`, a UUID4 kept in `~/.opik-mcp/install-id` with mode 0600. If
  HOME cannot be written it is the nil UUID and `install_id_kind` is
  `fallback` (`src/opik_mcp/analytics/identity.py`;
  `test_install_id_kind_flags_the_shared_nil_sentinel`).
- `env_id_sha256`, a digest of the OS machine id. If none can be read the
  digest is left out and `env_id_kind` is `unknown`, which is where
  containers usually land (`_detect_env_id` in
  `src/opik_mcp/analytics/environment.py`;
  `test_env_id_emits_nothing_when_unreadable`).
- `workspace` and `workspace_kind`. The inbound `Comet-Workspace` header
  outranks the setting. In order: an unfilled template value (`template`), a
  named workspace (`configured`), the workspace the backend resolved
  (`resolved`), the literal `default` (`placeholder`), nothing (`unknown`, no
  `workspace` field) (`AnalyticsClient._resolve_workspace`;
  `test_an_inbound_header_outranks_the_process_setting`).
- `workspace_id`: the configured UUID, else the one the backend bound to the
  credential.
- `api_key_sha256` when `OPIK_API_KEY` is set
  (`test_raw_api_key_never_in_payload_or_logs`).
- `user_id_kind` and `identity_lookup`.

Top-level `user_id` is the caller's Comet login in plaintext when it can be
resolved (`user_id_kind=comet_user`), else the install id
(`user_id_kind=install_id`) (`AnalyticsClient._resolve_user`;
`test_login_is_emitted_plaintext_as_the_top_level_user_id`). The login
appears in no other field (`test_the_login_never_leaks_into_any_other_field`),
and a workspace name is never used as `user_id`
(`test_workspace_name_is_not_an_identity_resolver`). `identity_lookup` is
`resolved`, `none_expected` (no credential presented) or `miss` (a credential
presented and not resolved) (`caller_identity_with_outcome` in
`src/opik_mcp/caller_identity.py`;
`test_a_forwarded_api_key_on_a_hosted_server_is_a_miss`). With an API key in
settings the lookup runs in the background, so a new process's first events
can say `miss`. How identity is resolved is
[hosted-auth](../hosted-auth/design-doc.md)'s.

Per-request fields (`AnalyticsClient._per_request_props`): `auth_mode` on
every event, from the inbound bearer or else from settings
(`test_stdio_auth_mode_api_key_when_env_key_set`); `token_sha256` for an
OAuth bearer only (`test_oauth_token_sha256_hashed_not_raw`);
`request_workspace` from the header; and `mcp_session_sha256`, a hash of
`Mcp-Session-Id`. Tool events run in a session task without that header, so
the digest is found by the credential that opened the session
(`test_mcp_session_digest_lands_when_the_context_var_is_empty`). stdio gets
none (`test_no_session_digest_is_invented_for_stdio`).

### Per-event properties

`opik_mcp_server_started` adds `has_workspace`, `has_api_key`,
`has_default_project`, `analytics_enabled`, `install_id_freshly_generated`,
`lifecycle_source`, `oauth_configured`, `resource_uri_scheme`,
`dns_rebinding_protection`, `allowed_hosts_is_default` and `auth_mode`
(`server_started_props` in `src/opik_mcp/analytics/boot_props.py`), plus the
environment fingerprint (`collect_environment_fingerprint`):

- `is_ci` is `true` when any of `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`,
  `BUILDKITE`, `CIRCLECI`, `JENKINS_URL` is set. It labels events and does not
  suppress them (`test_detect_ci_true_when_any_known_var_set`).
- `is_container` is checked on Linux only, from `/.dockerenv` or the tokens
  `docker`, `containerd`, `kubepods` in `/proc/1/cgroup`; elsewhere it is
  `unknown` (`test_detect_container_unknown_on_non_linux`).
- `is_codespaces`, `is_gitpod`, `stdin_is_pipe`, `stdout_is_pipe`, and
  `launch_method` (`uvx`, `pipx`, `venv`, `system`, `unknown`) from
  `sys.executable`.
- `parent_process` is the immediate parent's command name matched against an
  allowlist. It is frozen, so under `uvx` and on Windows it says `other`
  (`test_parent_process_stays_other_for_uv_launcher`). `host_process` steps
  over a `uv` launcher to the grandparent and works on Windows; `launcher` is
  `uv` or `none` (`test_detect_host_process_walks_through_uv_to_the_host`).

A detector that raises gives its fallback value and the event still goes out
(`_safe`; `test_collect_environment_fingerprint_never_raises`). No detector
returns a raw path, user name or command line
(`test_detect_parent_process_never_leaks_raw_name`).

`opik_mcp_server_shutdown` adds `lifespan_seconds_bucket` and two flags from
`src/opik_mcp/analytics/transport_probe.py`: `first_rpc_received` (a
`tools/list` or tool call arrived) and `session_reached` (a tool call ran).
With the fingerprint, they tell a process that was only probed from a
handshake that stalled and from a real session.

`opik_mcp_session_initialized` adds the host fields from
`collect_session_props` in `src/opik_mcp/analytics/mcp_client_info.py`. They
are `mcp_host` and `host_llm_family` (frozen), and `mcp_client` and
`client_llm_family` (these recognise `claude-ai` and `Visual Studio Code`, and
say `absent` when `clientInfo` is missing). It also has `mcp_client_version`
and `mcp_protocol_version`, kept only in semver or `YYYY-MM-DD` shape, and the
`caps_*` flags (`test_classify_mcp_client_separates_absent_from_other`).

`opik_mcp_tool_called` adds `tool_name`, `success`, `duration_ms`, the
per-call context from `call_context_props` (four environment fields and four
host fields, cached), and on failure `error_kind`, `exception_type`,
`cause_type` and `http_status`. On success it also carries the tool's props
from `src/opik_mcp/server.py`. `_read_props` gives `entity_type`, `id_kind`
and `field_count`. `_list_props` gives the shape of the call: which filter
fields and sort field were used, never their values, whether a window or
search was set, and `empty` and `source_defaulted` when the call reached a
page (`test_list_props_record_the_search_shape_without_values`).
`_write_props` gives `operation`, `is_batch`, `batch_size_bucket`, `dry_run`
and `had_idempotency_key`; `_schema_props` gives `operation`.
`_read_skill_props` gives `skill` (`unknown` for an unknown name),
`request_shape` and `is_reference`, never the document path
(`test_read_skill_never_emits_the_document_path`). If a props function
raises, `instrument_tool` skips its props and sends the event.

### Error kinds

`error_kind` is a value of `ErrorKind` in `src/opik_mcp/error_kinds.py`:
`auth`, `validation`, `not_found`, `permission`, `timeout`, `network`,
`upstream_5xx`, `cancelled`, `unknown`, and `invalid_config` at startup.
`bucket_exception` in `src/opik_mcp/analytics/errors.py` decides it:

1. A bare `ToolError` is unwrapped through `__cause__`, or an unsuppressed
   `__context__`, to the real cause, with a bounded depth and a cycle check
   (`test_unwrap_follows_cause_through_tool_error`,
   `test_unwrap_handles_cycle_without_infinite_loop`). `exception_type` keeps
   the wrapper class and `cause_type` the leaf.
2. A status on the instance, from `httpx.HTTPStatusError` or
   `BackendError.extra["backend_error"]["status"]`, goes through
   `bucket_http_status`: 401 `auth`, 403 `permission`, 404 `not_found`, 408
   and 504 `timeout`, other 4xx `validation`, 5xx `upstream_5xx`
   (`test_backend_error_instance_status_routes_bucket`).
3. Otherwise the class's `error_kind: ClassVar[ErrorKind]`, declared in
   `src/opik_mcp/opik_client.py`, `src/opik_mcp/writes/errors.py`,
   `src/opik_mcp/read_list/errors.py`, `src/opik_mcp/read_list/uri.py`,
   `src/opik_mcp/config.py` and `src/opik_mcp/skills_catalog.py`. A subclass
   overrides its parent (`test_subclass_classvar_shadows_parent`).
4. Otherwise pydantic `ValidationError` is `validation`,
   `httpx.TimeoutException` is `timeout`, another `httpx.RequestError` is
   `network`, and the rest is `unknown`.

Host cancellation is `cancelled`
(`test_cancelled_error_sets_error_kind_cancelled`). The classifier never
reads `str(exc)` or `exc.args` (`test_bucket_exception_never_reads_args_or_str`).

### Sentry

`instrument_tool` reports a failure to Sentry unless its kind is `auth`,
`permission`, `validation`, `not_found` or `cancelled`, or the cause is a
`MissingConfigError` (`_USER_SIDE_ERROR_KINDS` in
`src/opik_mcp/analytics/wrappers.py`; `test_sentry_skips_user_side_failures`).
The capture is tagged with the tool name, `error_kind`, `cause_type`, the
tool's props and the bucketed host; its transaction is the tool name and its
fingerprint adds the tool name to default grouping (`_report_to_sentry`).
`setup_sentry` turns off default integrations, tracing and
`send_default_pii`. The scope user is the workspace UUID, else the workspace
name, else the install id (`test_setup_sentry_user_id_prefers_workspace_uuid`).
`before_send` caps events per process (`test_before_send_caps_events_at_30`).

### Delivery

`track_event` builds the event in the caller's task, while the request's
context variables are still set, and puts it on a bounded queue; a full queue
drops it (`test_queue_full_drops_silently`). A daemon thread POSTs it, which
works before an event loop exists
(`test_track_event_safe_without_running_event_loop`), and retries a failed
POST on a short backoff before logging and dropping it
(`test_retry_succeeds_on_second_attempt`). The startup-error and shutdown
emitters call `flush()` with a deadline so the thread is not killed mid-POST
(`test_clean_exit_flushes_with_configured_deadline`).

### Privacy

Every property is a boolean string, an allowlisted value, a bucket or a
SHA-256 digest, apart from the workspace fields and the top-level login
(module docstring of `src/opik_mcp/analytics/events.py`). Free text, paths,
argument values, exception messages and raw tokens are never sent.
`tests/test_analytics_privacy.py` pushes canary strings through every props
function, the wrapper and the Sentry path; it cannot see inside
`_build_event`, which `tests/test_analytics_client_build_event.py` covers.

### Keeping telemetry off in tests

- `tests/conftest.py` sets `OPIK_MCP_ANALYTICS_ENABLED=false` and
  `OPIK_MCP_SENTRY_ENABLED=false` at import with `setdefault`, for the whole
  test process. Subprocesses and the e2e suite inherit them.
- `tests/test_telemetry_disabled_in_tests.py` fails if either variable is
  missing or not false, if `Settings` stops reading it, or if both defaults
  turn off.
- `setup_sentry` and `before_send` also refuse under pytest (`_in_pytest` in
  `src/opik_mcp/error_tracking.py`). That check reads `sys.modules` and
  `PYTEST_CURRENT_TEST`, which a subprocess with a clean environment does not
  have, so the environment switch is the main guard.
- The autouse fixture `_reset_analytics_wrappers_state` in `tests/conftest.py`
  resets the analytics singleton, both dedup sets, the transport-probe flags,
  the detector caches, the resolved identities and the lifecycle variable
  before and after each test. Analytics tests turn it on themselves and
  inject a recorder or a mocked HTTP client.

To see real payloads, run `scripts/capture_bi_listener.py`, which prints each
POST body, and point `OPIK_MCP_ANALYTICS_URL` at it.

## How it works

| Module | Owns |
|---|---|
| `src/opik_mcp/analytics/__init__.py` | `get_analytics()` (a process-wide `lru_cache` singleton), `track_event`, `reset_analytics_for_tests`. Call sites use `track_event` and never build an `AnalyticsClient`. |
| `src/opik_mcp/analytics/client.py` | `AnalyticsClient`: event building, queue, worker, retry. |
| `src/opik_mcp/analytics/events.py` | Event names, the `Literal` allowlist for every enum property, bucket functions. |
| `src/opik_mcp/analytics/wrappers.py` | `instrument_tool`, `install_tools_listed_emitter`, the Sentry skip list. |
| `src/opik_mcp/analytics/boot_props.py` | Properties shared by the `main()` and lifespan paths so the two cannot differ; the lifecycle sentinel. |
| `src/opik_mcp/analytics/environment.py` | Fingerprint detectors, `env_id`, `cached_call_context_env`. |
| `src/opik_mcp/analytics/mcp_client_info.py` | Host classifiers, `collect_session_props`, `call_context_props`. BI and Sentry both use them, so a raw `clientInfo.name` buckets the same way on both. |
| `src/opik_mcp/analytics/identity.py` | `install_id`, `api_key_sha256`, `OPIK_MCP_VERSION`. |
| `src/opik_mcp/analytics/errors.py`, `src/opik_mcp/error_kinds.py` | The error taxonomy. `error_kinds.py` is a leaf so any layer can import `ErrorKind` without a cycle. |
| `src/opik_mcp/analytics/transport_probe.py` | The two handshake flags. |
| `src/opik_mcp/error_tracking.py` | Sentry setup, scope, cap, capture. |
| `src/opik_mcp/server.py` (part) | The `_*_props` functions, `@instrument_tool` on the five tools, `AuthRejectionMiddleware`, the emits in `_make_composed_lifespan`. |
| `src/opik_mcp/__main__.py` (part) | `_emit_startup_error`, `_emit_server_shutdown`, `_build_fallback_analytics_client`, `_bucket_transport_exception_type`, the emit calls in `main()`. |

A tool call runs the `server.py` handler wrapped by `instrument_tool` →
`_maybe_emit_session_initialized` → the tool → on failure `bucket_exception`,
`derive_http_status` and perhaps `_report_to_sentry` →
`track_event(EVENT_TOOL_CALLED, props)` → `AnalyticsClient._build_event` adds
the common and per-request blocks → queue → worker → POST.

Boundaries:

- The inbound-auth context variables, `classify_bearer`,
  `caller_identity_with_outcome` and `credential_digest` belong to
  [hosted-auth](../hosted-auth/design-doc.md). This feature reads them and
  sends only digests of credentials.
- `installation_type` lives in `src/opik_mcp/config.py` so BI and Sentry share
  one value without an import cycle. Process start, transports and the bind
  preflight are [runtime](../runtime/design-doc.md)'s.
- `page_facts`, `filter_field_names` and `sort_field_label`, read by
  `_list_props`, belong to [tool-surface](../tool-surface/design-doc.md).
- The exceptions that declare `error_kind` stay in their own modules;
  [writes](../writes/design-doc.md) documents `WriteError` and `BackendError`.

## Decisions

- No ADR covers analytics. The reasons below come from code comments and PRs.
- Telemetry never fails a tool call or hides a startup error. `track_event`
  swallows every error, and emit sites on an error or shutdown path catch
  `BaseException` (`AnalyticsClient.track_event`, `_emit_startup_error`,
  `_maybe_emit_session_initialized`).
- A failed POST is retried because the first cold POST often lost the
  DNS and TLS race and `server_started` never arrived (#140).
- The hosted `--factory` entrypoint skips `main()`, so its boots and auth
  failures were not visible. The `build_app()` lifespan now emits lifecycle
  events when `main()` did not, and `opik_mcp_auth_rejected` was added (#148).
- `error_kind` is a `ClassVar` on each exception class, read with `getattr`,
  so a new exception declares its own bucket and no `isinstance` chain grows.
  `BackendError` is bucketed by the status on the instance, because one class
  wraps many upstream statuses (#128, #130).
- A bare `ToolError` is unwrapped before bucketing. Before that, every tool
  failure showed as `unknown` (#127).
- Classifications that dashboards already use are frozen (`parent_process`,
  `mcp_host`, the POSIX `launch_method` table). Corrections go into new fields
  (`host_process`, `launcher`, `mcp_client`) so no existing series moves (#165).
- `user_id` is the caller's Comet login in plaintext, the one personal
  identifier sent. The rest of the product already sends it, it is the
  warehouse's user key, and a digest could not be joined. `user_id_kind` says
  what the field holds (#161).
- `env_id_sha256` is a digest of the OS machine id and nothing user-derived.
  When none is readable the digest is left out; a hostname fallback was
  rejected because in a container it changes on every run (#165).
- `identity_lookup` and `install_id_kind` exist so that "no credential" and
  "credential not resolved" are not summed, and the shared nil install id can
  be excluded (#169).
- Sentry gets only failures that need a stack trace. User-side kinds are
  dropped at the capture site, and a per-process cap stops a loop from
  flooding the project (#125).
- Both channels default to on for users and are switched off in the test
  environment itself. The in-process pytest guard does not reach a subprocess
  started with a clean environment (#175).

## Proven by

- `tests/test_analytics_client.py`: wire shape, opt-out, queue and retry, workspace and user resolution, source handling.
- `tests/test_analytics_client_build_event.py`: common and per-request blocks, digests instead of credentials, the login only in `user_id`, `identity_lookup`, `install_id_kind`, `env_id`, the session digest.
- `tests/test_analytics_events.py`: event names, bucket thresholds, each `Literal` matches what its classifier can return.
- `tests/test_analytics_wrappers.py`: `tool_called` and `session_initialized`, error kinds through the wrapper, host classifiers, the Sentry skip list and tags.
- `tests/test_analytics_call_context.py`: the per-call context block and its caches.
- `tests/test_analytics_tools_listed.py`: the `tools/list` handler swap, one event per session.
- `tests/test_analytics_errors.py`: unwrapping, status mapping, `ClassVar` bucketing, no message reads.
- `tests/test_analytics_environment.py`: each fingerprint detector, the frozen fields, Windows paths, `env_id`.
- `tests/test_analytics_boot_props.py`: boot properties, `self-hosted` with a hyphen, the lifecycle sentinel.
- `tests/test_analytics_identity.py`: the install id file and its mode, the `api_key_sha256` transform.
- `tests/test_analytics_server_startup.py`: emits from `main()`, startup errors, the fallback client, Sentry on a transport crash.
- `tests/test_analytics_subprocess.py`: startup errors and opt-out in a real subprocess.
- `tests/test_analytics_lifespan.py`: shutdown reasons and flags, `main()` versus lifespan ownership.
- `tests/test_analytics_auth_rejected.py`: rejection reasons, skipped paths, no raw token.
- `tests/test_analytics_transport_probe.py`: the handshake flags.
- `tests/test_analytics_privacy.py`: no canary string reaches an event or a Sentry capture.
- `tests/test_error_tracking.py`: Sentry init, scope tags, the event cap, the pytest guard, capture scoping.
- `tests/test_telemetry_disabled_in_tests.py`: both switches are off for the test process and still map to `Settings` fields.

## Log

- 2026-09-21: list events carry `empty` and `source_defaulted` (#192).
- 2026-09-08: list events record the search shape (filter field names, sort field, window, search) without values (#185).
- 2026-09-01: `read_skill` props added; both telemetry switches set in the test environment and CI (#175).
- 2026-08-25: `install_id_kind` and `identity_lookup`, so hosted identity failures can be counted (#169).
- 2026-08-21: `mcp_session_sha256` reaches tool events on the hosted server (#166).
- 2026-08-21: `host_process`, `launcher`, `mcp_client`, `env_id_sha256` and the session digest added beside the frozen fields (#165).
- 2026-08-14: `workspace_kind` and the workspace precedence (#162).
- 2026-08-13: `user_id` becomes the caller's Comet login, with `user_id_kind` (#161).
- 2026-06-08: `opik_mcp_auth_rejected`, and lifecycle events from the `build_app()` lifespan (#148).
- 2026-06-02: failed POSTs are retried so `server_started` arrives (#140).
- 2026-05-27: per-call context block on `tool_called` (#132).
- 2026-05-25: Sentry error tracking (#125); environment fingerprint and lifecycle events (#123).
