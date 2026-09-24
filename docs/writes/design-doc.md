# writes

## Purpose

`write(operation, data)` is the one tool that changes anything in Opik, and
`schema(operation)` returns an operation's input on demand. Open this doc to
find out what happens between a `write` call arriving and the backend request,
what the caller gets back on success, and what an error looks like.

## What it does now

### The two tools

`write` takes four arguments (`write` in `src/opik_mcp/server.py`):

- `operation`: an entity and verb pair such as `thread.close` or
  `score.create`. The input schema advertises the registry's names as a JSON
  Schema `enum`, but the parameter is typed `str`. A host's schema check
  therefore lets an unknown name through, and the dispatcher answers it with
  the structured `unknown_operation` error (comment above
  `WRITE_OPERATION_ENUM`).
- `data`: an object for one write, or an array for a batch where the operation
  allows it. Two operations, `dataset_item.upsert` and
  `experiment_item.create`, always take one object with their list inside it.
- `idempotency_key`: optional. When set, it is sent to
  the backend as the `Idempotency-Key` header (`OpikClient.write_json` in
  `src/opik_mcp/opik_client.py`). If an item's own `id` differs from it, the
  tool-level key wins and a warning is logged; the call does not fail
  (`_resolve_idempotency_key` in `src/opik_mcp/writes/dispatch.py`).
- `dry_run`: validate and check scope, then return the request the backend
  would receive without sending it.

The `write` description is generated from the registry at import: one line per
operation with its description, its required parent fields and `(batch ok)`
where batches are allowed, then notes on data shape, `dry_run` and error
recovery (`build_write_description` in `src/opik_mcp/writes/description.py`).
A new registry entry appears in the description without anyone editing prose.

`write` carries the tool annotations `readOnlyHint=False,
destructiveHint=True`, because `trace.update` and the thread and issue state
changes rewrite records that already exist (`_WRITES` in
`src/opik_mcp/server.py`). `schema` is marked read-only.

`schema(operation)` is a lookup with no backend call
(`run_schema` in `src/opik_mcp/writes/schema_tool.py`). For a write operation
it returns `operation`, the JSON Schema of `data`, one example, the
`oauth_scope`, `supports_batch`, `parent_id_fields`, `failure_modes` and the
description. `failure_modes` lists the universal issue codes (Pydantic codes
such as `missing` and `extra_forbidden`, plus `empty_batch`,
`batch_unsupported` and `batch_too_large`) followed by the operation's own
codes, so a caller can include a required field before its first attempt. The
same tool also answers `list.<entity>` keys with the list tool's filter and
sort reference; that half belongs to
[tool-surface](../tool-surface/design-doc.md). An unknown key gets the same
`unknown_operation` envelope `write` returns.

### Operations

The registry is `_REGISTRY` in `src/opik_mcp/writes/registry.py`; the
authoritative list is `WRITE_OPERATIONS` there, and `schema(operation)` gives
each one's input. The families:

- Observability: `trace.create`, `trace.update`, `span.create`,
  `score.create`, `comment.create`. Scores and comments attach to a `trace`,
  `span` or `thread` through `target` and `target_id`.
- Threads: `thread.close`, `thread.open`.
- Evaluation: `prompt_version.save`, `dataset.create`,
  `dataset_item.upsert`, `experiment.create`, `experiment_item.create`. Their
  hooks are documented in
  [experiment-flows](../experiment-flows/design-doc.md).
- Diagnostics: `agent_insights_issue.resolve`, `.close`, `.reopen`, and
  `agent_insights_job.enable`, `.trigger`. Their hooks are documented in
  [diagnostics](../diagnostics/design-doc.md).

No operation deletes anything. There is no `*.delete` entry in the registry.

Unverified: the reasons deletes, `thread.delete`, `experiment.finish` and
feedback-score deletion were not built. Nothing in the code or git history
records them.

### A call, step by step: `write('thread.close', data)`

Take `data = {"thread_id": "conv-1", "project_name": "demo"}`.

1. Lookup. `thread.close` is found in `WRITE_REGISTRY`. An unknown name raises
   `unknown_operation` with the full `valid_operations` list and, when
   `difflib` finds a close match, `did_you_mean` (`UnknownOperationError` in
   `src/opik_mcp/writes/errors.py`).
2. Shape validation. `data` must be an object or array. `thread.close` has
   `supports_batch=False`, so an array is refused with `batch_unsupported`
   (`test_thread_close_rejects_batch`). The object is parsed by the
   `ThreadClose` model in `src/opik_mcp/writes/models.py`, which forbids
   unknown fields and requires `thread_id` plus `project_name` or
   `project_id`; without a project it fails with `thread_project_missing`
   (`test_thread_close_requires_project`). If the entry has a `validate_fn`,
   it runs next; `thread.close` has none.
3. Scope check. The operation's `oauth_scope`, `trace_span_thread_log` here,
   must be in the scopes passed to the dispatcher, or the call fails with
   `authorization_denied` and `required_scope` before any network call
   (`test_thread_close_requires_log_scope`). See "Scopes" below for what the
   server passes today.
4. Prepare. On a live call the dispatcher builds the client and runs the
   entry's `prepare_fn` if it has one. `thread.close` has none. A dry run
   skips this step and never builds a client.
5. Build. `threads.build_thread_lifecycle` returns the fixed endpoint
   `/v1/private/traces/threads/close` with the model dumped without `None`
   values as the body: `{"thread_id": "conv-1", "project_name": "demo"}`
   (`test_thread_close_puts_fixed_endpoint_with_body`). The method is the
   registry's `PUT`. On a dry run the tool returns here with `would_call`
   (`test_thread_close_dry_run_reports_fixed_path`).
6. Send. `OpikClient.write_json` sends the request with the caller's
   credentials and workspace headers and does not raise on 4xx or 5xx. How
   the client and headers are set up is in
   [runtime](../runtime/design-doc.md).
7. Finalize. A non-2xx status becomes `backend_error`. A 2xx becomes the
   success envelope. `decorate_fn` (`observability.decorate_with_page`) then
   adds a `url` if it can (see "Links on success").

### What the caller gets on success

```json
{"ok": true, "operation": "thread.close", "method": "PUT",
 "path": "/v1/private/traces/threads/close", "status": 204,
 "batch": false, "item_count": 1, "backend_body": "...", "url": "..."}
```

`backend_body` is the response parsed as JSON when it is JSON, else its text
(`safe_body` in `src/opik_mcp/writes/wire.py`). `item_count` is the number of
records sent. For the envelope operations it counts the records inside the
envelope (`envelope_items_key` on `WriteOperation`;
`test_an_envelope_upsert_counts_its_cases_not_its_envelope`).

A dry run returns `{"dry_run": true, "would_call": {...}}` with `method`,
`path`, `body_size`, `batch`, `item_count` and the `body` itself, so the caller
can check what an operation translated before committing
(`test_dry_run_returns_would_call_without_be`). An operation whose preview
cannot be exact adds a `note`. A thread comment is one: the live call resolves
the `thread_id` string to the thread's model UUID, and a dry run does not
(`threads.comment_dry_run_note`). A dry run still fails on invalid data
(`test_dry_run_does_not_mask_validation_failure`).

### Links on success

Observability and thread writes add one `url` per call, also for a batch
(`decorate_with_page` in `src/opik_mcp/writes/operations/observability.py`):

- A single trace create or update links to that trace, a single span to its
  trace with the span selected, a thread close or open to that thread.
- A score or comment on a trace or thread links to that trace or thread. A
  span annotation links to the project's spans view, because the annotation
  names the span but not its trace.
- A batch, or a single write whose id the backend assigns, links to the
  project's Logs page with the matching view.

The project id comes from what a `prepare_fn` resolved or from `project_id` in
the payload. A `project_name` alone is never resolved for a link, because that
would be a backend call after the write already succeeded, and its failure
would make a successful write look failed. So `thread.close` with only
`project_name` succeeds with no `url`
(`test_a_project_named_but_not_identified_is_not_resolved_for_a_link`,
`test_a_write_whose_project_is_unknown_still_succeeds_unlinked`). How the URLs
are built is in [tool-surface](../tool-surface/design-doc.md)
(`src/opik_mcp/read_list/ui_links.py`).

### What the caller gets when data fails validation

Every failure is a `WriteError` subclass. `run_write` in
`src/opik_mcp/writes/write_tool.py` turns it into a `ToolError` whose text is
the error as compact JSON, so the host marks the result as an error and the
model reads the JSON on its next turn. There is one text block and no
`structuredContent` copy (`src/opik_mcp/writes/errors.py` module docstring).

A `validation_failed` body:

```json
{"error": "validation_failed", "operation": "thread.close",
 "issues": [{"field": "", "message": "thread_project_missing: pass ...",
             "code": "thread_project_missing"}],
 "expected_schema": {"...": "the operation's JSON Schema"},
 "example": {"thread_id": "...", "project_name": "..."}}
```

- `issues` has one entry per Pydantic error, with a dotted `field` path. In a
  batch the path starts with the failing index, such as `[3].name`, and
  validation stops at the first failing item (`_stage2_validate` in
  `src/opik_mcp/writes/dispatch.py`).
- `code` is the Pydantic error type, unless a model validator's message starts
  with `<code>: `; then that prefix is the code, such as `project_xor`,
  `combined_tag_modes`, `thread_project_missing` or `target_id_not_uuid`
  (`_convert_pydantic_errors`).
- `expected_schema` is the same JSON Schema `schema(operation)` returns
  (`test_validation_failed_expected_schema_matches_schema_tool`).
- `example` is the registry's example, or one built for the case. A single
  `score.create` on a thread has no backend route, so the error returns the
  array form rebuilt from the caller's own fields
  (`observability.validate_scores`). An empty batch gets the example wrapped
  in a list.

Checks an operation makes before sending that its model cannot express, such
as "thread not found" while resolving a thread comment, raise the same
`validation_failed` shape through `refuse` in `src/opik_mcp/writes/wire.py`.

The other error codes, all from `src/opik_mcp/writes/errors.py`:

| `error` | When | Extra fields |
|---|---|---|
| `unknown_operation` | name not in the registry | `valid_operations`, `did_you_mean` if close |
| `batch_too_large` | array longer than `BATCH_LIMIT`, checked before any parsing | `size`, `limit` |
| `authorization_denied` | the operation's scope is not granted | `required_scope` |
| `backend_error` | the backend answered non-2xx, or a pre-send resolve failed | `backend_error: {status, body, method, path}` |

`backend_error` carries the backend's body as received
(`test_backend_4xx_wraps_with_body_verbatim`). On a 401 under an OAuth bearer
token, the message adds the same token-expired hint the read path uses, and
the dispatcher drops the cached OAuth validation so the next request meets the
401 that makes the host refresh (`_stage4_finalize`,
`test_401_under_oauth_bearer_says_token_expired`). Details of that flow are in
[hosted-auth](../hosted-auth/design-doc.md).

`BatchPartialFailureError` (`batch_partial_failure`) is defined but never
raised; each batch is one backend request that succeeds or fails as a whole.

### Scopes

Each operation names one scope from `src/opik_mcp/writes/scopes.py`, and
`schema` reports it. The dispatcher's scope stage works when given a narrower
set (`test_partial_scope_advertised_but_rejected_per_op`), but the `write`
tool in `src/opik_mcp/server.py` calls `run_write` without `scopes`, so the
default `ALL_WRITE_SCOPES` applies and every call passes the stage. The
module docstring of `scopes.py` records this as the state until session-bound
scopes exist. The Diagnostics operations use `project_data_view`, the
permission the backend already requires on those endpoints, so enabling
Diagnostics through the MCP grants nothing the REST API does not.

## How it works

Call path:

```
server.write                         src/opik_mcp/server.py
  -> write_tool.run_write            src/opik_mcp/writes/write_tool.py
     (WriteError -> ToolError JSON)
  -> dispatch.run_write              src/opik_mcp/writes/dispatch.py
     lookup -> validate -> authorize -> prepare -> build -> send -> retry -> finalize -> decorate
  -> OpikClient.write_json           src/opik_mcp/opik_client.py
```

`schema` goes `server.schema` to `run_schema` in
`src/opik_mcp/writes/schema_tool.py` and never leaves the process.

Modules under `src/opik_mcp/writes/`:

- `registry.py`: `WriteOperation`, one frozen entry per operation with model,
  endpoint, method, scope, batch support, example, `failure_modes` and hooks.
  Exposed read-only as `WRITE_REGISTRY` (a `MappingProxyType`).
- `models.py`: one Pydantic model per operation, `MODELS` and `EXAMPLES`.
  Shared rules live on mixins: `_StrictBase` forbids extra fields,
  `_TagsMixin` rejects `tags` together with `tags_to_add` or
  `tags_to_remove`, `_ProjectMixin` rejects both project fields,
  `_RequiredProjectMixin` requires one, and `_AnnotationTarget` requires a
  UUID for trace and span targets and a project for thread targets.
- `dispatch.py`: the stages. It names no operation.
- `wire.py`: the hook signatures (`ValidateFn`, `BuildFn`, `PrepareFn`,
  `RetryFn`, `DecorateFn`, `DryRunNoteFn`), `WireRequest`, `BuildContext`,
  `dump` (JSON-mode dump without `None`, so datetimes use the ISO form the
  backend parses), `refuse`, `safe_body` and `TARGET_PATH`.
- `errors.py`: the `WriteError` hierarchy and the JSON envelope.
- `scopes.py`, `description.py`, `schema_tool.py`, `write_tool.py`.
- `operations/observability.py`: build hooks for traces, spans, scores and
  comments, `validate_scores`, `decorate_with_page`.
- `operations/threads.py`: `build_thread_lifecycle`,
  `resolve_comment_thread_id` (the comment route takes the thread's model
  UUID while callers pass the `thread_id` string), `comment_dry_run_note`.
- `operations/evaluation.py` and `operations/diagnostics.py`: owned by
  [experiment-flows](../experiment-flows/design-doc.md) and
  [diagnostics](../diagnostics/design-doc.md).

Hooks. The dispatcher runs the same stages for every operation. What differs
is a hook on the registry entry, implemented in `writes/operations/`:

- `validate_fn`: a rule the model cannot express, after Pydantic.
- `build_fn`: models to request. Without one, the request is the endpoint plus
  the dumped single item.
- `prepare_fn`: live path only. Resolves an id the wire needs and the caller
  does not carry, or refuses the call.
- `retry_fn`: reinterprets a backend answer and may send a follow-up.
- `decorate_fn`: adds to the success envelope.
- `dry_run_note_fn`: says what a preview cannot show.

A builder may override the method. `build_trace_update` sends a batch as
`POST /v1/private/traces/batch` because that route is a POST-only upsert
(`test_trace_update_batch_coerces_patch_to_post`).

Error kinds for analytics are `ClassVar`s on each `WriteError` subclass
(`error_kind`, `http_status`). `BackendError` is bucketed by the status on the
instance, because each instance wraps a different upstream status
(`_instance_http_status` in `src/opik_mcp/analytics/errors.py`).
`BatchPartialFailureError` has no `ClassVar` because it is never raised. The
bucketing itself belongs to [analytics](../analytics/design-doc.md).

Boundaries. Credentials, workspace headers and the HTTP client belong to
[runtime](../runtime/design-doc.md) and
[hosted-auth](../hosted-auth/design-doc.md). UI link construction belongs to
[tool-surface](../tool-surface/design-doc.md).

Smoke scripts, run by hand and not by CI:

- `scripts/smoke_mcp_session.py`: drives the in-process server over an MCP
  session, calls `schema` and then `write` with `dry_run=True` for each
  operation, and prints the path and body.
- `scripts/smoke_wire_capture.py`: sends the dataset operations through the
  real HTTP layer against a `respx` mock and checks the wire field names and
  the mapping of type test_suite to the backend's `evaluation_suite`.
- `scripts/smoke_live_be.py`: calls the dispatcher against a live backend
  using the credentials in the environment.

## Decisions

- One `write` tool over an operation registry, plus `schema`, replaced the
  narrow per-verb write tools on 2026-05-18
  ([ADR 0003](../decisions/0003-five-tool-surface.md)). A new operation is a
  registry entry, and the description, enum and `schema` pick it up.
- The dispatcher names no operation. Per-operation logic lives in hooks in
  `writes/operations/`
  ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md), #186).
- `operation` is advertised as an enum but typed `str`, so a wrong name
  reaches the dispatcher and gets the `unknown_operation` envelope with the
  valid names (#99).
- Every validation error carries the expected schema and a working example, so
  the model can correct the call in one more turn without calling `schema`
  first (`test_llm_recovery.py`, #99).
- Errors are one JSON text block, because `structuredContent` support across
  hosts was uneven (`src/opik_mcp/writes/errors.py`;
  [ADR 0003](../decisions/0003-five-tool-surface.md)).
- Error kinds are `ClassVar`s on the error classes. `BackendError` is bucketed
  by the status on each instance. `BatchPartialFailureError` has no bucket
  until something raises it (#130).
- A success result carries one UI link per call, and a link never costs a
  backend call after the write (#201).
- Thread annotations and lifecycle take the `thread_id` string plus a project.
  The dispatcher resolves the model UUID where the comment route needs it, so
  callers use one identifier (#152).
- Each operation declares an OAuth scope, and the dispatcher checks it. The
  server passes all scopes until sessions carry real ones
  (`src/opik_mcp/writes/scopes.py`).
- Not built: any delete operation.

## Proven by

- `tests/test_writes/test_dispatch.py`: the exact request each operation sends
  (path, method, body, batch versus single endpoint), scope refusal before any
  network call, dry run, the idempotency header, backend error wrapping, item
  counts through envelopes, and the Diagnostics hooks.
- `tests/test_writes/test_models.py`: each bundled example validates, and each
  operation's main negative case gives the expected issue code and field.
- `tests/test_writes/test_data_rules.py`: the tag and project rules on every
  model that uses the mixins, and the tool-level idempotency key winning over
  an item id.
- `tests/test_writes/test_registry.py`: the registry, server enum, models,
  examples and description agree; every method is valid; batch entries have an
  endpoint pair; scopes are known.
- `tests/test_writes/test_schema_tool.py`: an unknown key raises a `ToolError`
  chained from `UnknownOperationError`, so analytics buckets it as validation.
- `tests/test_writes/test_dispatch_stays_generic.py`: the dispatcher names no
  operation, every hook comes from `writes/operations/`, and no new operation
  name appears at the root.
- `tests/test_writes/test_write_links.py`: one link per call, the link targets
  per operation, and no link without a known project id.
- `tests/test_writes/test_backend_error_oauth_hint.py`: a 401 under an OAuth
  bearer carries the token-expired hint, under an API key it does not.
- `tests/conformance/test_write_tool_surface.py`: over a real MCP session,
  `write` and `schema` are listed, the enum matches the registry, descriptions
  match byte for byte, and `schema` output matches the registry model.
- `tests/integration/test_llm_recovery.py`: a retry built from a
  `validation_failed` example passes validation and reaches the backend,
  including the thread score array form.
- `tests/test_analytics_errors.py`: the `ClassVar` bucket per `WriteError`
  subclass and `BackendError` bucketing by instance status.

## Log

- 2026-09-23: observability and thread writes return a UI link, so the caller knows where to look (#201).
- 2026-09-22: `item_count` counts the records inside an envelope; a large upsert had reported 1 (#199).
- 2026-09-17: `test_suite.create` and `test_suite_item.upsert` became `dataset.create` (with a type) and `dataset_item.upsert` (#190).
- 2026-09-10: operation logic moved from the dispatcher into registry hooks; Diagnostics job and issue operations added (#186).
- 2026-09-04: a 401 under OAuth carries the token-expired hint and clears the cached validation so hosts refresh (#182).
- 2026-07-24: `thread.close` and `thread.open` added with full thread support (#152).
- 2026-05-26: `WriteError` subclasses bucketed for analytics through `ClassVar`s; before, every write failure was unknown (#130).
- 2026-05-18: one `write` tool and `schema` replaced the narrow score and comment tools (#99).
