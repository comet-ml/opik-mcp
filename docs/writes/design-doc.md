# writes

## Purpose

`write(operation, data)` is the only tool that changes anything in Opik;
`schema(operation)` returns an operation's input. This doc answers what a write
promises, what comes back on success and failure, and where to change or add an operation.

## What it does now

### Inputs

- `operation` is an entity and verb pair such as `thread.close`. An unknown
  name gets the `unknown_operation` error.
- `data` is an object for one write, or an array for a batch where the
  operation allows it (`supports_batch`). `dataset_item.upsert` and
  `experiment_item.create` carry their records inside an envelope object.
- `idempotency_key` is sent as the `Idempotency-Key` header. If an item's own
  `id` differs, the tool-level key wins and a warning is logged.
- `dry_run` validates and checks scope, then returns the request unsent.
- `WRITE_OPERATIONS` in `src/opik_mcp/writes/registry.py` lists the operations.
  None deletes anything.

### Success

```json
{"ok": true, "operation": "thread.close", "method": "PUT", "path": "/v1/private/traces/threads/close",
 "status": 204, "batch": false, "item_count": 1, "backend_body": "...", "url": "..."}
```

- `item_count` counts the records sent, inside the envelope where there is one.
- `url` is one UI link per call on observability and thread writes: the row
  for a single write, the Logs view for a batch. It needs a known project id;
  a `project_name` alone gives no link.
- A dry run returns `{"dry_run": true, "would_call": {...}}` with method, path,
  body, item count and a `note` where the preview cannot be exact.

### Failure

`dispatch.run_write` raises every failure as a `WriteError`;
`write_tool.run_write` turns it into a `ToolError` whose text is compact JSON.

```json
{"error": "validation_failed", "operation": "thread.close",
 "message": "data does not fit 'thread.close'; retry write('thread.close', data=…) shaped like example, and schema('thread.close') returns the full schema.",
 "issues": [{"field": "", "message": "thread_project_missing: ...", "code": "thread_project_missing"}],
 "example": {"thread_id": "...", "project_name": "..."}}
```

- `field` is a dotted path, prefixed with the index in a batch (`[3].name`).
  Validation stops at the first failing item.
- `code` is the Pydantic error type, unless a validator message starts with
  `<code>: `, which then becomes the code (`thread_project_missing`).
- `example` is a working payload. The JSON Schema is not inlined; `message`
  names `schema(operation)`, which returns it. A check made before sending,
  such as a thread that is not found, returns the same `validation_failed`
  shape (`refuse` in `wire.py`), but its `message` is the check's own sentence
  and fix, repeated in the issue, rather than the schema-mismatch one.

```json
{"error": "backend_error", "operation": "trace.create",
 "message": "Opik rejected the data for 'trace.create' (400); fix it and retry write('trace.create', data=…).",
 "backend_error": {"status": 400}, "backend_message": "project not found"}
```

- `message` is one sentence per status and the call to retry
  (`_backend_sentence`); a 409 says the write conflicts with an existing
  record rather than that the data is bad. `backend_error` holds only the status, which
  analytics buckets on; the body, method and path are not carried.
- `backend_message` appears on a 400 or 422 only: the strings under the
  body's `errors` or `message`, cut at `_BACKEND_REASON_CHARS`
  (`backend_reason` in `src/opik_mcp/opik_client.py`). A non-JSON body or any other status has none.
- A Diagnostics enable whose 409 follow-up PATCH fails reports the PATCH's
  status, so a 401 keeps the credential hint and a 5xx says retry.

Other codes (`src/opik_mcp/writes/errors.py`): `unknown_operation` (with
`valid_operations`, `did_you_mean`), `batch_too_large` (over `BATCH_LIMIT`) and
`authorization_denied` (`required_scope`). The OAuth 401 hint is in
[hosted-auth](../hosted-auth/design-doc.md).

### Scopes

Each operation declares a scope, and the dispatcher compares it with the
scopes it is given. The `write` tool in `src/opik_mcp/server.py` gives none, so
the default `ALL_WRITE_SCOPES` applies and every call passes today.

## How it works

```
server.write                  src/opik_mcp/server.py
  -> write_tool.run_write     src/opik_mcp/writes/write_tool.py  (WriteError -> ToolError)
  -> dispatch.run_write       src/opik_mcp/writes/dispatch.py
     lookup, validate, authorize, prepare, build, send, retry, finalize, decorate
  -> OpikClient.write_json    src/opik_mcp/opik_client.py
```

- prepare: live calls only; a hook resolves an id the caller did not pass.
- retry: after the send, a hook may reread the answer and send a follow-up
  (a Diagnostics enable that gets 409 switches the existing job on).
- finalize: a non-2xx status becomes `backend_error`, a 2xx the success result.

- To change an operation's endpoint, method, scope or batch support, start at
  `src/opik_mcp/writes/registry.py`. Models and `EXAMPLES` are in
  `src/opik_mcp/writes/models.py`.
- To change what one operation sends or resolves, start at its hook in
  `src/opik_mcp/writes/operations/`. Hook types are in
  `src/opik_mcp/writes/wire.py`. Without a `build_fn` the request is the
  endpoint plus `items[0]`.
- Which write gets which link is decided by `decorate_with_page` and
  `_LOGS_VIEW` in `src/opik_mcp/writes/operations/observability.py`; the URLs
  are built by `src/opik_mcp/read_list/ui_links.py`
  ([tool-surface](../tool-surface/design-doc.md)).

Evaluation hooks: [experiment-flows](../experiment-flows/design-doc.md).
Diagnostics hooks: [diagnostics](../diagnostics/design-doc.md). `write_json`:
[runtime](../runtime/design-doc.md). Error kinds:
[analytics](../analytics/design-doc.md). Every write against a real Opik:
[live-e2e](../live-e2e/design-doc.md).

### Adding an operation

Add a model, an example in `EXAMPLES` and a registry entry; the enum,
description and `schema` follow. Also:

- Regenerate the `write` input snapshot with `UPDATE_SNAPSHOTS=1`
  (`tests/conformance/test_schema_snapshots.py`) and say why in the PR.
- The longer description counts against `SURFACE_BUDGET_BYTES` in
  `tests/conformance/test_tool_inventory.py`.
- A new observability or thread operation needs `decorate_fn` set and its name
  in the `decorate_with_page` branches and `_LOGS_VIEW`, or it gets no link or
  only the bare Logs page. No test catches a missing entry.

## Decisions

- One `write` tool over a registry replaced the per-verb tools, so a new
  operation is a registry entry ([ADR 0003](../decisions/0003-five-tool-surface.md), #99).
  Its logic lives in hooks, so the dispatcher stays generic
  ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md), #186).
- `operation` is advertised as an enum but typed `str`, so a wrong name reaches
  the dispatcher and gets the valid names back (#99).
- A validation error carries the issues and a working example, and names
  `schema(operation)` for the schema. Inlining the schema made a failed write
  cost up to 3,219 characters (OPIK-8496; it was inlined from #99).
- A backend error carries no body, method or path, since the body is
  untrusted text. The capped `backend_message` on a 400 or 422 is the
  exception, in its own field so it is never read as ours (OPIK-8496).
- A link never costs a backend call after the write. A failed lookup would make
  a successful write look failed (#201).
- Thread writes take the `thread_id` string plus a project, and the comment
  hook resolves the model UUID, so callers use one identifier (#152).

### Traps

- `experiment_item.create` has `supports_batch=True` and no `build_fn`, so for a
  top-level array of envelopes the default builder sends only `items[0]` and
  drops the rest without an error. Known bug, not fixed here.
- `BatchPartialFailureError` is defined but never raised; each batch is one
  request. It inherits the base error kind `unknown`.
- A `trace.update` batch goes out as a POST to the batch route, overriding
  the registry's PATCH (`test_trace_update_batch_coerces_patch_to_post`).
- Unverified: a live thread comment's link may carry the thread's model UUID,
  because `resolve_comment_thread_id` replaces `target_id` before the link is
  built. `test_commenting_on_a_thread_links_to_that_thread` skips that step.

## Proven by

- The request each operation sends, scope refusal, dry run, idempotency and
  backend errors: `tests/writes/test_dispatch.py`; the envelope shapes,
  `test_a_validation_failure_points_at_schema_instead_of_inlining_it`,
  `test_a_backend_rejection_is_one_sentence_and_the_retry_call`,
  `test_a_failed_follow_up_after_409_reports_its_own_status`,
  `test_a_409_write_says_it_conflicts_rather_than_bad_data`,
  `test_a_write_backend_message_is_one_line_without_double_quotes`,
  `test_a_write_backend_message_is_capped`, `test_a_500_write_has_no_backend_message`. Models and their issue
  codes: `tests/writes/test_models.py`, `tests/writes/test_data_rules.py`.
- Registry, enum, models and description agree: `tests/writes/test_registry.py`,
  and over a real MCP session `tests/conformance/test_write_tool_surface.py`.
- The dispatcher names no operation: `tests/writes/test_dispatch_stays_generic.py`.
- Links: `tests/writes/test_write_links.py`. OAuth 401 hint: `tests/writes/test_backend_error_oauth_hint.py`.
- A retry built from an error's example succeeds: `tests/writes/test_recovery_envelope.py`.
- An unknown `schema` key: `tests/writes/test_schema_tool.py`.

## Log

- 2026-09-25: error envelopes shrink to a sentence and the retry call; the schema is behind `schema(op)`, `backend_error` is `{status}`, and a 400/422 adds a capped `backend_message` (OPIK-8496).
- 2026-09-23: observability and thread writes return a UI link, so the caller knows where to look (#201).
- 2026-09-22: `item_count` counts records inside an envelope; a large upsert had reported 1 (#199).
- 2026-09-17: `test_suite.*` writes became `dataset.create` and `dataset_item.upsert`; a suite is a dataset (#190).
- 2026-09-10: operation logic moved into registry hooks; Diagnostics operations added (#186).
- 2026-05-18: one `write` tool and `schema` replaced the score and comment tools (#99).
