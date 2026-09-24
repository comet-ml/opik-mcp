# diagnostics

## Purpose

This feature exposes Opik's Diagnostics (Agent Insights) to the agent. A
project's recurring failures, already grouped by the Diagnostics job, can be
listed and read, and the agent can enable the job, trigger a scan and move an
issue between open, resolved and closed. Open this doc to learn what an issue
list says when it is empty or out of date, how a project name becomes the id
these endpoints need, and what each Diagnostics write does.

## What it does now

### Listing issues

`list('agent_insights_issue', project_id=… | project_name=…)` returns one
page of the project's issues with the columns `id`, `name`, `severity`,
`status`, `total_occurrences`, `latest_count` and `last_seen`
(`HANDLER.list_extra_fields` in
`src/opik_mcp/read_list/entities/agent_insights_issue/entity.py`). Long fields
such as the cause stay out of the table; `read` returns them.

- Project scope is required. Without either argument the call is refused
  (`test_list_issues_requires_project_scope`).
- Only open issues are listed by default. `status='resolved'` or
  `status='closed'` lists the others (`list_page` in `entity.py`).
- No sort is sent. Rows keep the backend's order (most recently seen, then
  total occurrences), which is the ranking the Diagnostics page shows
  (`list_page`, `test_list_issues_renders_diagnostics_columns_in_backend_order`).
- `name` is dropped because the backend has no name filter
  (`test_list_issues_ignores_name_filter`).
- Counts are all time. `since`/`until` narrow them, and the list tool cuts
  the window to UTC dates and sends them as `from_date`/`to_date`
  (`run_list` in `src/opik_mcp/read_list/list_tool.py`). The header echoes
  the dates that were sent.
- `issue` is accepted as a short name for the entity type but is not listed
  in the tools' `entity_type` enum (`ENTITY_ALIASES` in
  `src/opik_mcp/read_list/registry.py`).

### What an empty list says

An empty page starts with "No agent_insights_issues found." and then one or
two sentences on why it is empty (`diagnostics_state_hint` in
`src/opik_mcp/read_list/entities/agent_insights_issue/state.py`). The server
checks the deployment toggle first and then reads the project's Diagnostics
job:

| Situation | What the reply says |
|---|---|
| The deployment has no Diagnostics (the toggle is off) | Diagnostics is not available here and cannot be enabled through the MCP. No write is offered. |
| The project has no job (the job read returns 404) | Diagnostics is not enabled for this project, with a `write('agent_insights_job.enable', {"project_id": …})` call to copy, and a trigger call for the first scan. |
| The job's status is `disabled` | Diagnostics is turned off for this project, with the same two calls. No switch-off date is given, because the job's `last_updated_at` changes on any scan or trigger. |
| Enabled, no `last_scan_at` | No completed scan yet. A scan started in the last few minutes may still be running; otherwise trigger one. |
| Enabled, last scan older than `STALE_AFTER` before the window end (`test_empty_issues_stale_boundary_is_24_hours`) | The scan is stale. It offers a trigger if the window is still open. If the window has already closed, it gives a bounded `list('trace', project_id=…, since=…, until=…)` call instead, because a trigger only rescans the `TRIGGER_COVERS` window back from now (`test_no_trigger_is_offered_for_a_window_that_already_ended`). |
| Enabled and scanned recently | No open (or resolved, or closed) issues, with the last scan time. "In the requested window" is added when the caller passed a window. |

Staleness is measured back from the end of the requested window (`until`,
or now when there is none). A job with a `last_failure_reason` adds "The last run
failed: …". When the Opik UI URL is known, the reply ends with a link to the
project's Diagnostics page (`project_page_url` in
`src/opik_mcp/read_list/ui_links.py`). An unknown status is reported as
"open" and the caller's string is never echoed (`_KNOWN_STATUSES` in
`state.py`).

Every part of this is added to a list the backend already returned. If the
job read fails, the note is left out and the plain "No
agent_insights_issues found." reply stays (`_read_job`, `issue_page_note`
in `state.py`).

### What a non-empty list says

A non-empty page ends with "Report covers data through <last scan>."
(`diagnostics_coverage_note` in `state.py`), because the rows only reflect
what the last scan grouped. If the requested window runs more than an hour
past that scan (`COVERAGE_GRACE`), the note names the uncovered stretch:

- a gap no wider than `TRIGGER_COVERS` while the window is still open gets a trigger call
  and a bounded `list('trace', …)` call;
- a larger gap gets only the `list('trace', …)` call, since a trigger cannot
  cover it (`test_issue_list_does_not_offer_a_trigger_that_cannot_close_the_gap`);
- a window that has already closed gets the gap named in absolute times and
  the `list('trace', …)` call.

This path does not check the deployment toggle, because the issues on the
page show that Diagnostics runs (`diagnostics_coverage_note` docstring). If
the job cannot be read or has no scan time, the page is returned without a
note (`test_issue_list_coverage_note_is_dropped_when_the_job_cannot_be_read`,
`test_issue_list_coverage_note_survives_a_job_without_a_scan_time`).

### Reading one issue

`read('agent_insights_issue', id, project_id=… | project_name=…)` makes one
backend call and returns five keys (`fetch` and `issue_links` in
`entity.py`):

- `issue`: the backend record without its `details` array, with severity,
  status, occurrence counts, cause and suggested fix;
- `example_trace_ids`: the union of every day row's
  `metadata.example_trace_ids`, deduplicated in first-seen order. A row
  without that metadata adds nothing and does not fail the read;
- `details`: the per-day rows as the backend returned them;
- `url`: the issue on the Diagnostics page, in the open view for an open
  issue and in the resolved view otherwise;
- `trace_url_template`: a logs link with `{trace_id}` to fill in, which
  names `logsType=traces` so the page opens on the traces view.

Trace bodies are not inlined. The caller opens an example with
`read('trace', id)`. Nothing is slimmed either, since these endpoints take no
`truncate` parameter ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
Both links are left out when the Opik URL or the workspace is unknown, for
example under an OAuth bearer whose workspace is not known locally
(`test_read_issue_omits_links_under_oauth_bearer_with_unknown_workspace`).

`since`/`until` on a read are cut to UTC dates the same way. The end is left
open when only `since` is given, so the read does not add an upper bound the
caller did not ask for (`read_window` on `HANDLER`, `run_read` in
`src/opik_mcp/read_list/read_tool.py`).

The id can also be an `opik://projects/<pid>/agent-insights-issues/<iid>`
URI or a pasted Diagnostics page link with `?issue=<iid>`, open or resolved
view. The project comes from the link and overrides a `project_name` passed
alongside it (`parse` in `src/opik_mcp/read_list/uri.py`,
`test_read_issue_link_project_overrides_explicit_name`).

### Project name resolution

The agent-insights endpoints take `project_id` only. `project_name` is
resolved in `src/opik_mcp/read_list/project_scope.py` so that every
project-scoped entity accepts the same two arguments:

- An explicit `project_id` wins and skips the lookup (`require_project_id`).
- The name is matched against the whole project name. An exact-case match
  wins; otherwise one case-insensitive match is used. A substring match is
  never used (`_lookup_project_id`).
- Several case-insensitive matches are refused with a list of `project_id`
  and name pairs to retry with.
- No match is refused with "Project '…' not found.", the closest name if
  there is one, and the project names that exist (`unknown_project_message`).
- A successful lookup is cached for a few minutes, keyed by REST base,
  workspace, a hash of the credential and the name, so tenants in one hosted
  process never share an entry. Misses and ambiguous names are not cached
  (`resolve_project_id`, `_cache_key`).

### Writes

Five operations go through `write(operation, data)`. Each takes
`project_id` or `project_name`; a payload with neither fails validation with
`project_scope_missing` (`AgentInsightsJobAction` and
`AgentInsightsIssueAction` in `src/opik_mcp/writes/models.py`). None of them
accepts a batch.

| Operation | Backend call | Behaviour |
|---|---|---|
| `agent_insights_job.enable` | `POST /v1/private/agent-insights/jobs/{project_id}` | Creates the job. On a 409 (job exists) it sends `PATCH` with `{"status": "enabled"}`, so repeating enable is safe. |
| `agent_insights_job.trigger` | `POST …/jobs/{project_id}/trigger` | Starts a scan over the backend's rescan window, which `TRIGGER_COVERS` in `state.py` mirrors (`test_job_trigger_starts_a_scan_and_reports_where_to_watch_it`). A 404 is refused as `diagnostics_not_enabled` with "enable it first". The result carries a note that the scan takes a few minutes and says where to read the issues. |
| `agent_insights_issue.resolve` | `PATCH /v1/private/agent-insights/issues/{issue_id}` | Body `{"project_id": …, "status": "resolved"}`. |
| `agent_insights_issue.close` | same | `status: "closed"`. |
| `agent_insights_issue.reopen` | same | `status: "open"`. |

(`_REGISTRY` in `src/opik_mcp/writes/registry.py`, `ISSUE_STATUS` and
`retry` in `src/opik_mcp/writes/operations/diagnostics.py`.)

- Both job operations are refused with `diagnostics_unavailable` when the
  deployment toggle is off. The issue operations skip that check, since an
  existing issue shows Diagnostics runs (`prepare_scope`).
- If the PATCH after a 409 also fails, the error carries both the original
  conflict and the PATCH error. The PATCH gets no idempotency key, so a
  replayed create key cannot return the stored 409
  (`test_job_enable_does_not_replay_the_create_key_on_the_patch`).
- Every result links the project's Diagnostics page. An issue move links the
  view the issue is now in: the open view after reopen, the resolved view
  after resolve or close (`decorate`).
- A dry run that was given a `project_id` shows the real path and body. With
  a `project_name` it shows a `{project_id}` placeholder and a note that the
  name is resolved at execution, and for job operations that the
  deployment check is not run (`dry_run_note`).
- The operation descriptions tell the agent to resolve or close an issue only
  when the user asks (`agent_insights_issue.resolve` and `.close` in
  `src/opik_mcp/writes/registry.py`). The instructions tell it to ask the user
  before enabling, because enabling creates a standing daily scan
  (`src/opik_mcp/instructions.py`). No test pins either sentence.
- All five need the `project_data_view` OAuth scope, the permission the
  backend already requires to read issues (`SCOPE_PROJECT_DATA_VIEW` in
  `src/opik_mcp/writes/scopes.py`).

### Deployment availability

`diagnostics_available` in
`src/opik_mcp/read_list/entities/agent_insights_issue/availability.py` reads
`GET /v1/private/toggles/`, which the UI also reads, and looks for
`ollieEnabled` or `ollie_enabled`. It runs uncached on every empty issue list
and job write. A failed read or a missing field counts as available.

### What the host is told

The Diagnostics paragraph in `render` in `src/opik_mcp/instructions.py`
routes "what is broken in production" to
`list('agent_insights_issue', project_name=…)` ahead of ranking raw traces.
It says open issues are the default and counts are all time unless
`since`/`until` narrow them, lists the empty-list states and the two job
writes, and says that a dated report which names an uncovered tail should be
completed with `list('trace', …, since=…)`. The `opik-diagnose` skill builds
on these reads; see [skills](../skills/design-doc.md).

## How it works

- `list`: `run_list` in `src/opik_mcp/read_list/list_tool.py` finds the
  handler in `src/opik_mcp/read_list/registry.py`, turns the window into
  dates because `from_date` is in `list_optional_kwargs`, and calls
  `list_page` in `entity.py`. `list_page` resolves the project through
  `require_project_id` and calls `OpikClient.list_agent_insights_issues`
  (`GET /v1/private/agent-insights/issues`) in `src/opik_mcp/opik_client.py`.
  After the table is rendered, the list tool calls the handler's
  `page_note_fn`, which is `issue_page_note` in `state.py`. It chooses the
  empty-list hint or the coverage note and reads the job through
  `get_agent_insights_job`.
- `read`: `run_read` in `src/opik_mcp/read_list/read_tool.py` parses a URI or
  pasted link, applies `HANDLER.read_window`, and calls `fetch`, which uses
  `get_agent_insights_issue` (`GET …/issues/{id}` with `project_id` as a query
  parameter). `issue_links` builds the links from `_project_id`, a private
  key the read tool removes before rendering.
- `write`: `src/opik_mcp/writes/dispatch.py` runs the registry entry's hooks
  from `src/opik_mcp/writes/operations/diagnostics.py`: `prepare_scope`
  (deployment check and project id), `build_job_action` or
  `build_issue_action`, `retry` (409 and 404 handling), `decorate`, and
  `dry_run_note`. The dispatcher names no Diagnostics operation
  ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)). The
  pipeline itself is in [writes](../writes/design-doc.md).

This feature owns the `agent_insights_issue` package (`entity.py`,
`state.py`, `availability.py`, reached through its `__init__.py`), the
Diagnostics hooks, models and registry entries on the write side, and
`src/opik_mcp/read_list/project_scope.py`. That module is shared: `scope_of`
also scopes `score_name`, `online_rule` and `project_metric`
([project-overview](../project-overview/design-doc.md)), and its cache is
cleared by the autouse fixture in `tests/conftest.py`. Only the Diagnostics
paragraph of `src/opik_mcp/instructions.py` is documented here; the rest is
[tool-surface](../tool-surface/design-doc.md). `state.py` names the job
operations as the constants `ENABLE_OP` and `TRIGGER_OP` because importing
`writes.registry` from `read_list` would be circular.

## Decisions

- The server resolves `project_name` to an id, because the agent-insights
  endpoints take only an id and every other project-scoped entity accepts a
  name. It matches the whole name the way the backend does on traces, so one
  argument selects the same project everywhere (#184).
- The resolved id is cached per credential as well as per workspace, because
  under OAuth passthrough the workspace may be unknown locally and the
  credential is what separates tenants (`_cache_key` in
  `src/opik_mcp/read_list/project_scope.py`).
- An empty issue list says which state the project is in and gives the
  write that fixes it, because "no issues" otherwise reads as an all-clear
  when Diagnostics was never on (#186, OPIK_8310).
- The deployment toggle is read fresh on every call, so a switch mid-session
  shows at once, and it fails open, so an older backend keeps working.
  Without it, a deployment without Ollie accepts enable and trigger and
  never scans (#186).
- A non-empty list states the date its report covers, because issues from
  an old scan otherwise read as the current state (#186).
- A trigger is offered only when it can cover the gap. It rescans the
  `TRIGGER_COVERS` window back from now, so for a window that has closed the server points to
  raw traces (`_trigger_reaches` in `state.py`).
- Open issues by default and all-time counts, which is what the Diagnostics
  page shows and what "what is broken" asks for (#184).
- A read returns example trace ids and does not inline the traces, so one
  read stays one backend call; the caller opens a trace by id
  ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- A repeated enable is safe: a 409 becomes a PATCH to `enabled` (#186).
- The write hooks sit behind the registry and the dispatcher stays generic;
  the read modules are one namespace under `read_list/entities/` (#186, #187,
  [ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)).
- The job writes need `project_data_view`, the backend's own permission for
  these endpoints, so enabling through the MCP grants nothing the REST API
  does not (`src/opik_mcp/writes/scopes.py`).

## Proven by

- `tests/test_read_list/test_list_tool.py`: list columns, order and
  defaults, windows as dates, name resolution and its cache
  (`test_list_issues_project_name_cache_is_per_credential`), every
  empty-list state, the deployment check
  (`test_deployment_gate_fails_open_when_toggles_cannot_be_read`), and the
  coverage note (`test_issue_list_does_not_offer_a_trigger_that_cannot_close_the_gap`).
- `tests/test_read_list/test_read_tool.py`: the five read keys
  (`test_read_issue_returns_issue_example_trace_ids_and_details`), the
  open-ended window, name reuse across calls, pasted links, and links by
  status (`test_read_issue_resolved_status_links_to_resolved_view`).
- `tests/test_read_list/test_uri.py`: issue URIs and pasted Diagnostics
  links, open and resolved.
- `tests/test_read_list/test_registry.py`: the entity is project scoped
  (`test_agent_insights_issue_is_project_scoped_and_listable`).
- `tests/test_writes/test_dispatch.py`: enable is safe to repeat
  (`test_job_enable_twice_in_a_row_is_safe`), the 409 follow-up, trigger
  without a job, refusal without Ollie, issue moves skip the check, links
  (`test_resolving_an_issue_links_the_view_it_moved_to`), dry runs, scope
  and batch refusal.
- `tests/test_opik_client_read.py`: the client methods' paths, filters and
  error mapping (`test_get_agent_insights_job_maps_404_to_not_found`).
- `tests/e2e/test_description_claims.py`: every sentence of the entity
  description, probed over stdio (`test_the_entitys_description_holds`).
- `tests/e2e/test_entity_links_surface.py`: a read issue links to its
  Diagnostics page over stdio
  (`test_a_read_carries_a_link_that_opens_the_thing_it_returned`).
- `tests/test_instructions.py`: the instructions name every Diagnostics
  state and the dated-report rule (`test_render_names_every_diagnostics_state`).

## Log

- 2026-09-23: the issue's trace link template names the traces view, so a handed-out link opens on it (#201).
- 2026-09-11: Diagnostics read modules moved to `read_list/entities/agent_insights_issue/`, one namespace per entity (#187).
- 2026-09-10: empty lists say why and full lists date their report, so neither reads as an all-clear (#186).
- 2026-09-10: enable, trigger, resolve, close and reopen writes added, so the agent can act on issues (#186).
- 2026-09-08: issues exposed through `read` and `list`; project names resolved to ids, which the backend needs (#184).
