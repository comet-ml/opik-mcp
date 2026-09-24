# diagnostics

## Purpose

This feature exposes Opik's Diagnostics (Agent Insights): list and read a
project's grouped failures, enable or trigger the job, and move an issue
between open, resolved and closed. It answers what an issue list says when it
is empty or out of date, how a project name becomes an id, and what each
write does.

## What it does now

### Listing issues

`list('agent_insights_issue', project_id=… | project_name=…)` returns one page
of the project's issues. `issue` is accepted as an alias.

- Project scope is required; without it the call is refused.
- Open issues only by default, as the Diagnostics page shows (#184).
  `status='resolved'` or `'closed'` (a top-level `list` parameter) lists the rest.
- No sort is sent, so rows keep the backend's order, which is the Diagnostics
  page's ranking. `name` is dropped; the backend has no name filter.
- Counts are all time. `since`/`until` narrow them, cut to UTC dates that the
  header echoes. The window end is `until`, or now when only `since` is given.

Three durations in `state.py` drive the notes: `STALE_AFTER` (how old a scan
may get), `TRIGGER_COVERS` (how far back from now a trigger rescans) and
`COVERAGE_GRACE` (how far a window may run past the last scan unremarked). A
window is still open when it ends within `COVERAGE_GRACE` of now; only then is
a trigger offered (`_trigger_reaches`).

An empty page says "No agent_insights_issues found." and then which state the
project is in (`diagnostics_state_hint`):

| State | The reply |
|---|---|
| Deployment toggle off | Unavailable here; cannot be enabled through the MCP. |
| No job (job read returns 404) | Not enabled, with an enable call and a trigger call to copy. |
| Job `disabled` | Turned off, with the same two calls. |
| Enabled, no `last_scan_at` | No completed scan yet; a recent trigger may still be running. |
| Last scan older than `STALE_AFTER` before the window end | Stale: a trigger if the window is still open, else a bounded `list('trace', …)` call. |
| Scanned recently | No issues of that status, with the last scan time. |

A failed last run adds "The last run failed: …". If the job cannot be read,
the plain reply stands. A non-empty page ends with "Report covers data through
<last scan>." (`diagnostics_coverage_note`). If the window runs more than
`COVERAGE_GRACE` past that scan, the note names the gap with a bounded
`list('trace', …)` call, plus a trigger call when the window is still open and
the gap is no wider than `TRIGGER_COVERS`. The note ignores the job's status, so
a disabled job with old open issues gets it and nothing says the job is off.

### Reading one issue

`read('agent_insights_issue', id, project_id=… | project_name=…)` is one
backend call; its keys are in the entity description in `entity.py`. Trace
bodies are not inlined. UI links are left out when the Opik URL or workspace
is unknown. A window is cut to UTC dates, its end left open when only `since`
is given. The id may be an `opik://projects/<pid>/agent-insights-issues/<iid>`
URI or a pasted Diagnostics link with `?issue=<iid>`, whose project overrides
a `project_name`.

### Project name resolution

`src/opik_mcp/read_list/project_scope.py` resolves `project_name`; these
endpoints take `project_id` only, and an explicit one wins. The whole name is
matched, exact case first, then one case-insensitive match, never a substring.
Several matches are refused with `project_id`/name pairs; none with "Project
'…' not found." and the closest name. Hits are cached for a few minutes.

### Writes

| Operation | Backend call | Notes |
|---|---|---|
| `agent_insights_job.enable` | `POST /v1/private/agent-insights/jobs/{project_id}` | A 409 becomes `PATCH {"status": "enabled"}`, so repeating is safe. |
| `agent_insights_job.trigger` | `POST …/jobs/{project_id}/trigger` | Scans the last `TRIGGER_COVERS`. A 404 is refused as `diagnostics_not_enabled`. |
| `agent_insights_issue.resolve` / `.close` / `.reopen` | `PATCH /v1/private/agent-insights/issues/{issue_id}` | Body `{"project_id": …, "status": …}`. |

- Each takes `project_id` or `project_name`; neither fails with
  `project_scope_missing`. No batch. All need the `project_data_view` scope.
- Job operations are refused with `diagnostics_unavailable` when the
  deployment toggle is off. Issue moves skip that check.
- The host is instructed to ask the user before enabling, since enabling
  creates a standing daily scan (`src/opik_mcp/instructions.py`).
- Results link the Diagnostics page in the view the issue is now in. A dry
  run given a `project_name` shows a `{project_id}` placeholder and says
  the name, and for job operations the toggle, are checked at execution.

## How it works

```
list  run_list -> list_page (entity.py) -> require_project_id
      -> list_agent_insights_issues; then issue_page_note (state.py)
read  run_read -> fetch + issue_links (entity.py)
write dispatch.py -> hooks in src/opik_mcp/writes/operations/diagnostics.py
```

- Columns or filters: `HANDLER` in `entity.py`, then the `list` signature and
  docstring in `src/opik_mcp/server.py`. A new parameter changes the schema
  snapshot and the tool byte budget.
- Empty-page and coverage text: `state.py`. Deployment check:
  `diagnostics_available` in `availability.py`.
- Writes: `diagnostics.py` and its entries in `src/opik_mcp/writes/registry.py`.
- `project_scope.py` also holds `remember_resolved_project`, which
  `list_tool.py` and `decorations.py` read. `scope_of` serves `score_name` and
  `online_rule`; `project_metric` calls `require_project_id` directly.
- Elsewhere: the write pipeline in [writes](../writes/design-doc.md), the tools
  and instructions in [tool-surface](../tool-surface/design-doc.md),
  `opik-diagnose` in [skills](../skills/design-doc.md).

## Decisions

- The server resolves `project_name` because every other project-scoped entity
  takes a name, matching the whole name as the backend does on traces (#184).
- The name cache key includes a credential hash, because under OAuth
  passthrough the workspace may be unknown locally (`_cache_key`).
- An empty list names its state and fix, and a full one dates its report,
  because otherwise either reads as an all-clear (#186).
- The deployment toggle (`ollieEnabled`; Ollie is the backend service that
  runs the scans) is read fresh and fails open, so a switch shows at once and
  an older backend keeps working. Without it, a deployment without Ollie
  accepts enable and trigger and never scans (#186).
- A read returns example trace ids and does not inline traces, so it stays one
  call ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- The writes use `project_data_view`, the backend's own permission for these
  endpoints, so the MCP grants nothing the REST API does not.

### Traps

- A backend without the Diagnostics API also answers the job read with 404,
  so the empty list says "not enabled". The trigger refusal names both causes;
  the empty-list hint names only "not enabled".
- The job's `last_updated_at` moves on every scan; it is not the switch-off date.
- The PATCH after a 409 gets no idempotency key, so a replayed create key
  cannot return the stored 409.
- `state.py` spells the job operations as `ENABLE_OP` and `TRIGGER_OP`, since
  importing `writes.registry` there is circular. Nothing pins them to the
  registry, although a comment in `state.py` says a test does.
- No test pins "ask before enabling" in `src/opik_mcp/instructions.py` or
  "only when the user asks" on resolve and close.

## Proven by

- List, empty states, coverage note: `tests/test_read_list/test_list_tool.py`.
- No trigger for a gap it cannot close:
  `test_issue_list_does_not_offer_a_trigger_that_cannot_close_the_gap`.
- Toggle fails open: `test_deployment_gate_fails_open_when_toggles_cannot_be_read`;
  cache per credential: `test_list_issues_project_name_cache_is_per_credential`.
- Read and links: `tests/test_read_list/test_read_tool.py`, `tests/test_read_list/test_uri.py`.
- Writes, 409 follow-up, refusals, dry runs: `tests/test_writes/test_dispatch.py`.
- Client paths: `tests/test_opik_client_read.py`. Over stdio:
  `tests/e2e/test_description_claims.py`, `tests/e2e/test_entity_links_surface.py`.
- Instructions: `test_render_names_every_diagnostics_state`.

## Log

- 2026-09-23: the issue's trace link template names the traces view, so a handed-out link opens on it (#201).
- 2026-09-11: Diagnostics read modules moved to `read_list/entities/agent_insights_issue/`, one namespace per entity (#187).
- 2026-09-10: empty lists say why and full lists date their report, so neither reads as an all-clear (#186).
- 2026-09-10: enable, trigger, resolve, close and reopen writes added, so the agent can act on issues (#186).
- 2026-09-08: issues exposed through `read` and `list`; project names resolved to ids, which the backend needs (#184).
