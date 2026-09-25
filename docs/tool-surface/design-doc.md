# tool-surface

## Purpose

The tool surface is what every host session loads from this server: the
`read`, `list` and `schema` tools, the `initialize` instructions, and the wire
contract the conformance suite pins. `write` is in
[writes](../writes/design-doc.md) and `read_skill` in
[skills](../skills/design-doc.md). This doc answers what a host receives, what
a read or a list promises, what is refused, and which test holds each promise.

## What it does now

### The advertised surface

- Exactly `read`, `list`, `write`, `schema` and `read_skill`, each with a
  title and all four hints. Only `write` is destructive (`_READS`, `_WRITES`
  in `src/opik_mcp/server.py`).
- `structured_output=False` everywhere: one text copy per answer.
- The surface and the instructions have byte ceilings (`SURFACE_BUDGET_BYTES`,
  `INSTRUCTIONS_BUDGET_BYTES` in `tests/conformance/test_tool_inventory.py`),
  with each raise recorded beside them. Input schemas are frozen in
  `tests/conformance/snapshots/` and change only with `UPDATE_SNAPSHOTS=1`.
- Claude Code cuts a description or the instructions at `DESCRIPTION_LIMIT`
  without warning. Tools over it today are strict expected failures in
  `OVER_THE_LIMIT` (`tests/conformance/test_tool_annotations.py`).

### initialize

`initialize` returns the name `opik-mcp` and the text of `render_instructions`
(`src/opik_mcp/instructions.py`): workspace and UI address, the default
project if set, tool selection, the link rule, and today's UTC date.

- Workspace precedence: inbound `Comet-Workspace` header, then the name
  introspected from an OAuth bearer, then the configured workspace, then
  `"default"` (`current_workspace` in `src/opik_mcp/read_list/ui_links.py`).
- Without a UI address it says "(Opik URL not configured)", which no test checks.
- The default project (`opik_default_project_name`) reaches the agent only
  here, as a name. Tools keep no state and never fall back to it, so the
  agent passes `project_name` on every call.
- On stdio the text is rendered once at import, so its date is the day the
  process started. The HTTP app calls `install_session_instructions`, which
  renders it per session to name that session's OAuth workspace, and keeps
  the boot text if a render fails.

### read

`read(entity_type, id, project_id?, project_name?, since?, until?, fields?)`:

```
[read: trace <id> | 1,234 tok | open as a link named 'my-trace']
{"trace": {...}, "spans": [...], "spansTruncated": false, "spanBodies": "...", "url": "..."}
```

- The header gives an estimated token count (`size_header`, `_CHARS_PER_TOKEN`
  in `src/opik_mcp/read_list/size.py`) and, when there is a `url`, the link
  text to use.
- `id` takes a UUID, a name (project, experiment, prompt, dataset), an
  `opik://` URI or a pasted Opik link: each entity declares its `uri_patterns`,
  and `parse` in `src/opik_mcp/read_list/uri.py` tries them by `uri_precedence`. A
  URI or link overrides `entity_type`, and a project-scoped one the project.
  Several name matches are refused with the candidates. No match falls through
  to a 404.
- Refused with the call that works: an unknown or list-only type, thread or
  agent_insights_issue without a project, and a window on an entity without
  `read_window`. The order is in `run_read`
  (`src/opik_mcp/read_list/read_tool.py`).
- The record asked for is never cut. Composite reads slim their children and
  count every cut, with the call that returns the rest:
  - `trace`: spans cut by the backend's `truncate=true`, bodies dropped past
    `SPANS_INLINE_CHARS` (the first span keeps its body), `spanBodies`, and
    `moreSpans` past `SPANS_INLINE_LIMIT`.
  - `thread`: one message per trace, sorted by `start_time`, same body budget,
    and `messagesError` when the traces call fails.
  - `prompt`: versions up to `VERSIONS_INLINE_LIMIT`, then `moreVersions`.
  - Others: [project-overview](../project-overview/design-doc.md), [experiment-flows](../experiment-flows/design-doc.md), [diagnostics](../diagnostics/design-doc.md).
- `fields=[…]` returns only the named dotted paths, uncut, and always keeps
  the id. It is marked `| projected` in the header and on the line under it.
  An unknown path is refused with the valid ones.

### list

```
[list: trace | filters: error_info is_not_empty AND source = "sdk" | sort: duration desc | since: 1h (2026-09-24T10:03Z)]
```

- The header echoes filters, sort, since, until, search and fields, in that order.
- `filters` is OQL, the grammar of the SDK's `search_traces(filter_string=…)`,
  checked in `src/opik_mcp/read_list/oql.py` against the entity's
  `Vocabulary` (`src/opik_mcp/read_list/handler.py`). All problems in a string come
  back in one `OQLError`; an unknown field gets the closest name and the valid
  ones (`test_unknown_field_suggests_the_closest_name_and_lists_the_fields`).
  Checked clauses go JSON-encoded in the `filters` query parameter, except
  the vocabulary's `param_fields`, which become parameters of their own.
  `schema("list.<entity>")` lists fields, operators and sortable names.
- `sort` is one field, `asc` or `desc`. `since`/`until` take `30m`, `1h`,
  `7d`, `2w` or an ISO-8601 instant with a zone. Only trace, span and thread
  take a window or `search`; other types refuse both and name what works.
- trace, span and thread need `project_id` or `project_name`. A misspelled
  name gets the closest project name back.
- An entity with its own runner (`project_metric`, the dataset comparison) takes the call from `run_list`.
- Columns are id, name, `list_extra_fields`, then the sort and filter fields.
  Cells past `_TRUNCATE_AT` are cut and counted; `url` cells never are.
  `fields=[…]` picks the columns and lifts the cut.
- The order of checks and calls is in `run_list` (`src/opik_mcp/read_list/list_tool.py`).

An empty page with a zero total carries at most one hint, picked by
`_empty_message` in this order: the entity's `page_note_fn` note
(Diagnostics); with `since`, the project's last trace when it is before the
window; under the `sdk` default, the rows other sources hold; with `name`, the
rows without it; with `filters`, the rows without them. A failed probe is
skipped.

A read carries a `url`, a `url_absent` sentence when the record has no page,
or nothing when the UI base or workspace is unknown. A trace, span or thread
page carries one `url_template` filled from each row: the entity's
`row_link_template` hook, turned into a page note by `page_note_of` in
`src/opik_mcp/read_list/decorations.py`. An entity with no page of its own
declares a `view_page`, and a case or prompt version a `parent_page`.

## How it works

```
server.py read / list (FastMCP tool, instrument_tool wrapper)
  -> read_list/read_tool.py run_read  |  read_list/list_tool.py run_list
  -> read_list/registry.py ENTITY_REGISTRY[entity_type]  (an EntityHandler)
  -> read_list/entities/<entity>.py  fetch_fn / list_fn / run_fn / link_fn / page_note_fn
  -> opik_client.py  -> Opik REST API
```

`schema("list.*")` goes `writes/schema_tool.py run_schema` → `read_list/reference.py list_reference`.

Where to start:

- A trace filter or sort field: add it to `filter_fields` and `sort_fields`
  of the `Vocabulary` in `src/opik_mcp/read_list/entities/trace.py`, mirroring
  opik-backend's `TraceField` enum and `TraceSortingFactory` (span and thread
  have their own). A field the backend takes as a query parameter also goes in
  `param_fields`. The registry indexes vocabularies by name (`VOCABULARIES`),
  and the `schema` reference follows by itself.
- An entity: a `HANDLER` (`EntityHandler` in `src/opik_mcp/read_list/handler.py`)
  in `read_list/entities/`, registered in `src/opik_mcp/read_list/registry.py`.
- A column or an empty-page hint: `src/opik_mcp/read_list/list_tool.py`.

Root modules stay generic ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md));
`entity_names_at_root` in `tests/repo/ratchets.json` is empty and stays so: a root table
for one entity is a missing hook on `EntityHandler`.

`decorations.block` runs an optional part of a read under a deadline. A
failure or timeout becomes `{"error": "Could not load …"}` and the read still
returns. Its main user is project-overview.

Boundaries:

- `client_for_call` and the longer `search` timeout:
  [runtime](../runtime/design-doc.md).
- Credentials and OAuth workspace resolution:
  [hosted-auth](../hosted-auth/design-doc.md).
- `instrument_tool` and the `_*_props` functions:
  [analytics](../analytics/design-doc.md). It buckets failures by the
  `EntityArgValidationError` subclass, so every argument error raises one.
- `project_scope.py`: [diagnostics](../diagnostics/design-doc.md).

## Decisions

- Every token on the surface and in an answer has to pay for itself, so both
  have ceilings and every read states its size
  ([ADR 0001](../decisions/0001-context-budget-first.md)).
- The record comes back whole and only children are slimmed. Compression
  tiers were removed because they cut answers the caller could not get back.
  `fields=[…]` filters and always declares itself (#187, #197,
  [ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- One OQL grammar, the SDK's, with tables from the backend so a string that
  validates here does not return 400 (#185).
- trace, span and thread lists add `source = "sdk"`, like the Logs page, so
  evaluator, playground and experiment traffic does not crowd out the
  application. A filter on a parent id (`PARENT_ID_FIELDS`) turns it off:
  experiment traces never have source `sdk`, so the default hid a whole
  drill-in. The metric runner follows the same rule.
- `search` on a type without it is refused. It used to return an unfiltered
  page that an agent read as the result (`_search_refusal`).
- One copy of each answer, because `structuredContent` doubled every string
  on the wire. Links are never guessed (#201).
- Not built: a size header on `list`, and a result cap through
  `_meta["anthropic/maxResultSizeChars"]` (open in ADR 0001 and ADR 0002).

### Traps

- The frozen schemas hold argument descriptions but not the `read` and `list`
  docstrings. Only the byte budget and the description limit check those.
- A trace with no `project_id`, or whose span call fails, comes back with
  empty `spans` and no notice.
- `from_time`/`to_time` are UUIDv7 bounds on the record id, so a list window
  filters on creation time. An exact `start_time` bound goes in OQL.
- The backend ignores an unknown sort field without an error, so sorts are
  checked locally. On a large workspace it can drop sorting and return an
  empty `sortable_by`; the header then calls the page unsorted.
- The last-trace hint reads one page of projects (`project_rows` in
  `src/opik_mcp/read_list/project_scope.py`). With only a `project_id`, a
  project outside that page gets the bare "No traces found."
- Under an OAuth bearer with no resolved workspace, `link_workspace` returns
  `None` and project-scoped links are left out, because the configured
  workspace would point into the wrong one.
- The UI serves retired link shapes through a shim that fills in the
  reader's last project, so an old-shape link opens the wrong project. Every
  link goes through `project_page_url`, which accepts only `ProjectArea`.

## Proven by

- Tool set, hints, budgets, description limit, no entity resources: `tests/conformance/`,
  `test_no_entity_resources_advertised`.
- Instructions: `tests/server/test_instructions.py`; per session,
  `test_initialize_names_oauth_workspace`; agreeing with `tools/list`,
  `tests/hermetic/test_stdio_session.py`.
- Reads and slimming: `tests/read_list/test_read_tool.py`; the uncut
  record, `test_a_huge_record_comes_back_whole`.
- Lists, the `sdk` default and empty-page hints:
  `tests/read_list/test_list_tool.py`, `tests/read_list/test_list_filters.py`.
- OQL, its reference and `fields`: `tests/read_list/test_oql.py`,
  `tests/read_list/test_list_schema.py`, `tests/read_list/test_fields.py`.
- Links: `tests/read_list/test_link_shape.py`, `tests/read_list/test_ui_links.py`,
  `tests/hermetic/test_entity_links_surface.py`.
- Namespaces and description claims: `tests/read_list/test_modular.py`,
  `tests/hermetic/test_description_claims.py`.

## Log

- 2026-09-24: description-limit check; titles and hints counted in the surface budget (#202).
- 2026-09-23: a UI link on every answer; the duplicate `structuredContent` copy removed (#201).
- 2026-09-22: inline budget on children's bodies, stated in the answer (#199).
- 2026-09-21: `fields=[…]` on `read` and `list`, so the caller names what comes back (#197).
- 2026-09-11: compression tiers removed, since they cut answers the caller could not get back (#187).
- 2026-09-08: `filters`, `sort`, `since`/`until` and `search` on `list`, so one call answers most questions (#185).
- 2026-09-01: `read_skill` added; the instructions stopped describing an unadvertised tool (#175).
- 2026-06-25: instructions rendered per session, to name the OAuth workspace (#151).
