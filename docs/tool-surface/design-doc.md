# tool-surface

## Purpose

The tool surface is what every host session loads from this server: the
`read`, `list` and `schema` tools, the `initialize` instructions, and the wire
contract the conformance suite pins. Open this doc to find out how a read or a
list turns arguments into backend calls and back into text, what a host
receives when it connects, and which tests hold each of those in place.
`write` and the write half of `schema` are in [writes](../writes/design-doc.md);
`read_skill` is in [skills](../skills/design-doc.md).

## What it does now

### The five tools and what every host sees

The server advertises exactly `read`, `list`, `write`, `schema` and
`read_skill` ([ADR 0003](../decisions/0003-five-tool-surface.md),
`test_tools_list_advertises_exactly_the_phase_one_surface`). Each one declares a
title and all four behaviour hints. `read`, `list`, `schema` and `read_skill`
are read-only and non-destructive. `write` is marked destructive because some
operations rewrite existing records (`_READS` and `_WRITES` in
`src/opik_mcp/server.py`, `test_the_hints_match_what_each_tool_does`).

Every tool is registered with `structured_output=False`. An answer arrives
once, as text, with no `outputSchema` and no `structuredContent` copy
(`test_no_tool_advertises_an_output_schema`,
`test_a_call_returns_one_copy_of_its_answer`).

The advertised surface (names, descriptions, input schemas, titles and hints)
has a byte ceiling, `SURFACE_BUDGET_BYTES` in
`tests/conformance/test_tool_inventory.py`. The comment beside it records every
raise and who spent the bytes. Input schemas are frozen as JSON files in
`tests/conformance/snapshots/`, and changing one needs `UPDATE_SNAPSHOTS=1`
(`test_tool_schema_matches_snapshot`). The frozen schemas include every argument
description, but the tool-level docstrings of `read` and `list` are not in
them. Those two docstrings are held only by the budget and by the
description-limit check below.

Claude Code cuts a tool description and the server instructions at its
description limit (`DESCRIPTION_LIMIT` in
`tests/conformance/test_tool_annotations.py`) without warning ([ADR 0001](../decisions/0001-context-budget-first.md) log,
checked live). `tests/conformance/test_tool_annotations.py` records this.
`test_the_description_arrives_whole` runs for each tool, and the tools over
the limit today are listed in `OVER_THE_LIMIT` as strict expected failures,
so the test starts failing once one of them fits. `test_the_instructions_arrive_whole`
is a strict expected failure for the same reason.

### What a host receives on initialize

`initialize` returns the server name `opik-mcp` and an instructions text
rendered by `render_instructions` in `src/opik_mcp/instructions.py`. The
text has these parts:

- The workspace and the Opik UI address. The workspace is the inbound
  `Comet-Workspace` header, then the name introspected from an OAuth bearer,
  then the configured workspace, then `"default"` (`current_workspace` in
  `src/opik_mcp/read_list/ui_links.py`;
  `test_render_inbound_workspace_header_outranks_resolved`,
  `test_render_prefers_resolved_workspace_over_settings`). The UI address is
  the REST base with its `/api` suffix removed, or the placeholder
  "(Opik URL not configured)" (`test_render_strips_api_suffix_from_opik_url`,
  `test_render_omits_the_trace_link_when_opik_is_unconfigured`).
- The user's default project, only when `opik_default_project_name` is set
  (`test_render_includes_default_project_name_when_set`,
  `test_render_omits_default_project_when_unset`). It is a name. The tools
  keep no state and have no fallback to it, so the agent has to pass
  `project_name` itself on every call (comment on the field in
  `src/opik_mcp/config.py`).
- Tool selection, one paragraph per tool: when to use `read` and `list`, the
  OQL filter form with one worked `list('trace', …)` call, where to start for
  "how is my project doing" and "what is broken in production", what the empty
  Diagnostics list means, the sorted list of write operations taken from the
  write registry, and the skill names taken from the skills catalog
  (`test_render_tells_the_agent_list_can_filter_sort_and_window`,
  `test_render_names_every_diagnostics_state`,
  `test_the_blob_names_the_skills_and_describes_none_of_them`).
- The link rule: put every returned `url` on the name of the thing, never
  print a bare address, and do it for every row mentioned
  (`test_the_link_rule_covers_every_row_and_not_just_the_first`).
- Today's date, in UTC (`test_render_includes_today_date`).

`render_instructions` accepts a `user_email`, but the server never passes
one, so the "as <email>" clause is always absent
(`test_render_omits_user_clause_when_email_unknown`).

When the text is rendered depends on the transport. On stdio it is rendered
once, when `src/opik_mcp/server.py` constructs `FastMCP` at import. On the
HTTP app, `install_session_instructions` wraps the low-level server's
`create_initialization_options` so the text is rendered again for each session
and can name the workspace resolved for that session. A render error there
keeps the text rendered at boot (`install_session_instructions`;
`test_initialize_names_oauth_workspace` in `tests/test_http_auth.py`). On
stdio, the date in the text is therefore the day the process started.

The instructions have their own byte ceiling, `INSTRUCTIONS_BUDGET_BYTES` in
`tests/conformance/test_tool_inventory.py`, measured with a long workspace
name and email. A host that defers the tool list still loads this text, so it
is budgeted separately ([ADR 0001](../decisions/0001-context-budget-first.md)).
Two e2e tests start a real stdio server and check that every tool the text
describes as a `- name:` bullet is advertised, and that `read_skill` is in both
(`test_the_blob_describes_no_tool_the_server_does_not_advertise`,
`test_read_skill_is_described_because_it_is_always_advertised`).

### read

`read(entity_type, id, project_id?, project_name?, since?, until?, fields?)`
returns one line of header and then the record as JSON:

```
[read: trace <id> | 1,234 tok | open as a link named 'my-trace']
{"trace": {...}, "spans": [...], "spansTruncated": false, "spanBodies": "...", "url": "..."}
```

The header gives the entity type, the id as passed, and an estimated token
count, the character count divided by `_CHARS_PER_TOKEN` (`size_header` and `estimate_tokens` in
`src/opik_mcp/read_list/size.py`). When the answer carries a `url`, the header
names the link text: the record's own `name`, or "Open in Opik" when it has
none (`_link_hint` in `src/opik_mcp/read_list/read_tool.py`,
`test_the_header_says_the_answer_has_a_link_and_what_to_call_it`).

The `id` argument takes a UUID, a name for nameable types, an `opik://` URI,
or a pasted Opik web link. A URI or link overrides `entity_type`, and a
project-scoped one also overrides the project and clears `project_name`
(`run_read`; `test_read_accepts_opik_uri_overriding_entity_type`,
`test_read_thread_via_web_url`). `src/opik_mcp/read_list/uri.py` recognises
these links: thread and Diagnostics links, an experiments compare link (read
as its first experiment), and trace links with `tls_trace=` or `trace_id=`. A
trace link wins over the compare view it sits on
(`test_a_trace_link_wins_over_the_compare_view_it_sits_on`). Collection URIs
are refused (`test_parse_rejects_collection_paths`).

The dispatch order in `run_read`:

1. Parse a URI or link if the id looks like one.
2. Resolve an alias: "issue", the pre-rename dataset names, and
   "dataset_item_case" (`ENTITY_ALIASES` in
   `src/opik_mcp/read_list/registry.py`). The aliases resolve but are not in
   the advertised enum (`test_the_alias_is_not_advertised_as_a_type_of_its_own`).
3. Refuse an unknown type with the readable types listed, and a list-only
   type (`prompt_version`, `project_metric`, `score_name`, `online_rule`) with
   a pointer to `list` (`test_read_rejects_list_only_entity`).
4. For an entity with `needs_project` (thread, agent_insights_issue), refuse
   when neither `project_id` nor `project_name` was given. The refusal
   includes a call to copy (`test_read_thread_requires_project`).
5. Refuse `since`/`until` for an entity with no `read_window`, and name the
   entities that take one (`test_read_window_refusal_names_every_entity_that_takes_one`).
   The window is resolved from one clock reading, so a relative start and an
   implied end cannot drift apart.
6. For a nameable entity (project, experiment, prompt, dataset) whose id is
   not a UUID, search by name. One match is used, several are refused with the
   candidates listed (a capped list, then a count of the rest), and none falls through to
   the fetch, which returns a 404 if the name does not exist
   (`_fetch_with_name_lookup`; `test_read_project_by_ambiguous_name_lists_candidates`,
   `test_more_than_ten_matches_say_how_many_more`). Entities marked `id_only`
   skip the lookup (`test_read_trace_skips_name_lookup_id_only_entity`).
7. Fetch through the entity's `fetch_fn`, attach the entity's `link_fn` output,
   and remove any underscore-prefixed keys the fetcher left for the link
   builder.
8. Serialise whole, or project to `fields` (below).

Backend errors become one `ToolError` sentence per status: not found,
permission denied, authentication failed (401 is about the credential and is
not called a permission problem), validation, or backend error
(`_format_client_error`; `test_read_surfaces_not_found_with_hint`).

The record asked for is never cut by this server
([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md),
`test_a_huge_record_comes_back_whole`). Composite reads inline their
children, and the children can be slimmed:

- `trace` (`src/opik_mcp/read_list/entities/trace.py`) returns
  `{trace, spans, spansTruncated}`. It fetches the trace, then up to
  `SPANS_INLINE_LIMIT` spans with `truncate=true`. The backend cuts span fields
  at about 10,000 characters and replaces base64 images with `"[image]"`.
  After `SPANS_INLINE_CHARS` of span bodies, the remaining spans keep their
  place in the tree and lose `input`, `output` and `metadata`
  (`drop_bodies_past` in `src/opik_mcp/read_list/slim.py`). The first span
  always keeps its body. `spanBodies` says how many spans were cut and how many
  lost their bodies, and names `read('span', id)` as the way to get one whole
  (`test_a_trace_counts_the_spans_that_lost_bytes`,
  `test_the_dropped_bodies_are_counted_and_one_call_away`). A longer tree adds
  `moreSpans` with the count and a `list('span', …, filters='trace_id = "…"',
  page=…, size=100)` call that continues where the inlined part stopped
  (`test_the_continuation_a_trace_hands_out_returns_the_same_set`). A trace
  with no `project_id`, or whose span call fails, comes back with an empty
  `spans` list and no notice (`test_read_trace_without_project_id_returns_empty_spans`,
  `test_a_trace_whose_spans_failed_to_load_carries_no_notice`).
- `thread` (`src/opik_mcp/read_list/entities/thread.py`) returns
  `{thread, messages, messagesTruncated}`. It fetches the thread record
  (slim, because `first_message` duplicates the first turn) and the traces
  with that `thread_id`, and turns each trace into one turn sorted by
  `start_time`, each carrying its `trace_id`. The same body budget and
  `messageBodies` notice apply, with `read('trace', trace_id)` as the way to
  get a turn whole. If the traces call fails, the answer has an empty
  `messages` list and a `messagesError` field, because an empty list would read as a thread
  with no messages (`test_read_thread_degrades_on_messages_failure`).
- `prompt` (`src/opik_mcp/read_list/entities/prompt.py`) returns
  `{prompt, versions, versionsTruncated}` with up to `VERSIONS_INLINE_LIMIT`
  versions and a `moreVersions` continuation past that.
- `span` is the flat record. Other entities are documented by their own
  features: project in [project-overview](../project-overview/design-doc.md),
  experiment and dataset in
  [experiment-flows](../experiment-flows/design-doc.md), agent_insights_issue
  in [diagnostics](../diagnostics/design-doc.md).

### fields

`fields=[…]` on `read` returns only the named paths, uncut
(`src/opik_mcp/read_list/projection.py`, from #197). Paths are dotted into
nested objects (`trace.output`) and arrays come whole (`spans`). The record's
id is always kept, as `id` or `<block>.id` for a composite
(`test_read_keeps_the_id_that_opens_the_next_level`). An unknown path is
refused with the valid paths listed
(`test_read_refuses_an_unknown_field_and_names_the_valid_ones`). A projected
answer is marked twice: `| projected` in the header, and a line under it
giving the kept and total counts, up to `NAMED_OMISSIONS` omitted names
(`src/opik_mcp/read_list/projection.py`) and the call
without `fields` (`marker`;
`test_a_projected_read_says_so_in_the_header_and_under_it`). The header only
names a link when the projected record still has a `url`
(`test_a_projection_that_drops_the_url_drops_the_header_promise_too`). An
empty list means the whole record (`normalise`).

### list

`list(entity_type, …)` returns a pipe-delimited table. The listable types are
the registry entries with a `list_fn` or a `run_fn` (`LISTABLE_TYPES` in
`src/opik_mcp/read_list/registry.py`). Entities with a `run_fn` answer the
whole call themselves when their `run_when_kwargs` are present, or always
when they declare none. `project_metric` and a `dataset_item` comparison work
this way and are documented in project-overview and experiment-flows. Unlike
`read`, a list answer carries no size header yet
([ADR 0001](../decisions/0001-context-budget-first.md)).

#### How list('trace', …) applies a filter

`run_list` in `src/opik_mcp/read_list/list_tool.py` runs these steps for
`list('trace', project_name='p', filters='error_info is_not_empty AND duration > 5000', sort='duration desc', since='1h')`:

1. It clears the per-call page facts and the project that the previous call
   resolved, resolves aliases and finds the `trace` handler. The handler has no
   `run_fn`, so the call takes the collection path.
2. It clamps `size` to `MAX_PAGE_SIZE` (`src/opik_mcp/read_list/paging.py`).
   Scope arguments are forwarded only when the handler declares them in
   `list_required_kwargs` or `list_optional_kwargs`. `project_name` goes along
   whenever `project_id` is accepted
   (`test_list_forwards_only_kwargs_the_entity_declares`). `name` is dropped
   for traces by the entity's `list_page`.
3. It checks scope. `trace` requires `project_id`, and `project_name`
   satisfies that. Without either the call is refused with
   `list('trace', project_id='<uuid>', …)` as the example
   (`test_list_traces_requires_project_id`,
   `test_list_traces_accepts_project_name_alternative`).
4. It compiles the OQL with `compile_filters('trace', …)` in
   `src/opik_mcp/read_list/oql.py`. The grammar is the one the Opik Python
   SDK's `search_traces(filter_string=…)` takes: `<field>[.<key>] <op> <value>`
   joined by `AND` only, strings in double quotes, numbers bare, `is_empty` and
   `is_not_empty` with no value, and `in`/`not_in` with a parenthesised list.
   The field and operator tables follow opik-backend's field enums and
   operator map, so an unknown field is refused here and never sent to the
   backend. Every problem in the string is collected into one `OQLError`: a
   syntax error with its position, an unknown field with the closest name and
   the field list, a bad operator with the field's type and valid operators,
   and a bad value with the expected format
   (`test_all_problems_are_reported_together`,
   `test_unknown_field_suggests_the_closest_name_and_lists_the_fields`). The
   result is a list of `{field, operator, key, value}` clauses.
5. It adds the source default. For trace, span and thread, when no clause
   names `source` or a parent id (`trace_id`, `thread_id`, `experiment_id`,
   `experiment_ids`), it appends `source = "sdk"`, the same default as the
   UI's Logs page (`SOURCE_DEFAULTED_ENTITIES`, `PARENT_ID_FIELDS`;
   `test_trace_filters_compile_to_the_backend_array_plus_sdk_source`,
   `test_naming_source_disables_the_default`). A filter on a parent id is
   read as a request for the whole parent, so it gets no default. Because of
   that, the `moreSpans` continuation returns the same set as the spans the
   trace read inlined.
6. It splits out parameter fields. `split_param_clauses` moves any clause the
   backend takes as its own query parameter out of the filter array (trace has
   none; experiment's `type`, `optimization_id` and `experiment_ids` are
   examples). The rest is JSON-encoded into `filters`.
7. It echoes the filters. The whole clause list, default included, is
   rendered back to OQL for the header, so the echo can be pasted into the
   next call (`render_filters`;
   `test_header_echoes_the_applied_filter_including_the_default`).
8. It sets `truncate=true` for trace, span and thread, since bodies never
   reach the table.
9. It resolves the window. `since`/`until` accept a relative span (`30m`,
   `1h`, `7d`, `2w`) or an ISO-8601 instant with a timezone
   (`src/opik_mcp/read_list/window.py`). They become `from_time`/`to_time`,
   which the backend applies to the record's creation time. An exact
   `start_time` bound is written in OQL instead. An `until` at or before
   `since` is refused locally
   (`test_until_before_since_is_rejected_locally`). The header echoes a
   relative bound as written plus the resolved minute
   (`test_relative_since_is_echoed_as_written_plus_the_bound`).
10. It forwards `search`, which only trace, span and thread accept. For any
    other type, `search` is refused with the nearest alternative (`name=` or a
    filter) (`test_search_on_a_type_without_it_is_refused_not_quietly_dropped`). A
    call with search gets a longer client timeout (`_SEARCH_TIMEOUT_S`).
11. It compiles the sort. `compile_sort` in
    `src/opik_mcp/read_list/sorting.py` takes one field and `asc` or `desc`,
    with `desc` as the default, and checks the field against the entity's
    sortable list, which includes `feedback_scores.<name>` and
    `usage.<key>`. The backend silently ignores an unknown sort field, so it
    is refused here with the sortable list
    (`test_sort_on_an_unsupported_field_lists_the_sortable_ones`).
12. It calls the backend. `client_for_call` opens one client for the whole
    call, and the handler's `list_page` calls `list_traces`
    (`GET /v1/private/traces`; see [runtime](../runtime/design-doc.md)). A 404
    that names a misspelled `project_name` is answered with the closest
    project name (`test_unknown_project_name_suggests_the_closest_one`). Other
    failures become one sentence through `_as_tool_error`: validation text as
    written, a typed backend error prefixed with "Failed to list traces", a
    timeout with advice to narrow, and an unreachable backend with the reason
    (`test_every_error_class_reaches_the_agent_through_the_tool`,
    `test_backend_timeout_is_reported_with_a_way_out`).
13. It checks whether the backend dropped the sort. An empty `sortable_by` in
    the response means the backend skipped sorting for a large workspace. The
    header says so, and the page is not treated as ordered afterwards
    (`test_header_echoes_the_sort_and_flags_a_dropped_one`).

The header is one line with the applied parts in this order: filters, sort,
since, until, search, fields
(`test_header_lists_filters_sort_window_search_in_that_order`):

```
[list: trace | filters: error_info is_not_empty AND duration > 5000 AND source = "sdk" | sort: duration desc | since: 1h (2026-09-24T10:03Z)]
```

#### The table

The table starts with `Found N traces (page P, showing C of N):`. Its columns
are `id`, `name` (left out for entities with `list_has_name=False`, such as
thread), the entity's `list_extra_fields` (for trace: `start_time`,
`duration`, `error_type`, `total_estimated_cost`), then the sort field and the
filter fields in order of first mention
(`test_filter_fields_become_columns_deduplicated_and_in_order`). Some filter
fields never become columns: bodies, `error_info`, `source`, `experiment_ids`
and `full_data` (`_NEVER_COLUMNS`). A field the filter pins to one value is
dropped, and the table says so, because the header already states the value
(`test_a_filter_pinned_to_one_value_adds_no_column_even_when_the_entity_would`).
`duration` and `ttft` are labelled `duration_ms` and `ttft_ms` and rounded
half-up. Timestamps are shown to the second. Newlines and pipes inside a value
are escaped so a row stays one row
(`test_a_line_break_or_a_pipe_in_a_value_stays_inside_its_cell`).

Values longer than `_TRUNCATE_AT` characters are cut, and a line under the
table counts them. `url` cells are never cut
(`test_list_truncates_long_values_at_sixty_chars`,
`test_a_url_column_is_never_cut_to_fit`). Every non-empty page ends with a
`fields:` line naming what its records carry
(`test_every_list_page_names_the_fields_its_records_carry`), then
`Use page=N for next S results.` when more pages exist, and then the entity's
page note.

`fields=[…]` on `list` replaces the column choice. The columns become the id,
the named fields in the order given, and the entity's
`list_identity_fields`. Cells are not cut, and a `projected:` line says what
was left out (`test_list_returns_the_named_columns_and_the_id_and_nothing_else`,
`test_projection_lifts_the_cell_cut_on_the_columns_it_keeps`). An unknown
name can only be refused once the page is in hand, so it is refused then, with
the valid names listed (`test_list_refuses_an_unknown_field_and_names_the_valid_ones`).

#### Empty pages

An empty page with a zero total explains itself where one extra call can find
the reason (`_empty_message`). Each probe is a one-row request:

- Under the `sdk` default, it counts the same listing without the default.
  When that finds rows, the answer says how many and which `source` values
  would show them (`test_empty_result_under_the_default_source_says_how_to_widen_it`).
- With the caller's `filters`, it counts without them and says how many rows
  the scope has (`test_a_filter_that_matched_none_of_a_project_with_traffic_says_so`).
- With `name`, it counts without the name
  (`test_a_name_search_that_finds_nothing_says_what_the_listing_holds`).
- With `since`, it reads the project's last trace time and says when the last
  trace arrived before the window, or that the project has no traces
  (`test_empty_windowed_page_reports_the_projects_last_trace`).

A probe that fails is skipped and the page still answers. An entity whose
`page_note_fn` returns a note for an empty page (Diagnostics issues) replaces
these probes with its own explanation.

### UI links

A read carries a `url` when one can be built from the record and the session,
a `url_absent` sentence when the record has no page, and nothing when the UI
base or workspace is unknown. A non-empty list page carries a way to open its
rows (#201). Links are built in `src/opik_mcp/read_list/ui_links.py` and are
never guessed:

- `project_page_url` builds `<ui>/<workspace>/projects/<project_id>/<area>`
  and accepts only the areas the UI serves (`ProjectArea`). It returns `None`
  when the UI base or the workspace is unknown, and raises for an area outside
  the set (`test_project_page_url_refuses_an_area_v2_serves_only_as_a_forwarder`).
  The UI keeps retired addresses working through a shim that fills in the
  last project the reader had open, so a link in an old shape opens the wrong
  project instead of failing. `tests/test_read_list/test_link_shape.py`
  checks that every link goes through this builder
  (`test_urls_are_built_only_where_the_area_set_is_enforced`).
- Under an OAuth bearer whose workspace was not resolved, the link workspace
  is `None` and project-scoped links are omitted (`link_workspace`;
  `test_link_workspace_is_none_for_oauth_bearer_with_unknown_workspace`).
- A trace links to the Logs page with the trace open. Without a project or a
  workspace it falls back to the backend's redirect endpoint, which finds both
  from the trace (`trace_links`;
  `test_trace_link_falls_back_to_the_redirect_when_the_workspace_is_unknown`).
  A span links to its trace with the span selected, and gets no link without
  its trace. A thread links to the Logs threads view.
- A prompt or dataset with no project has no page, and its read says so in
  `url_absent` (`scoped_entity_links`;
  `test_a_workspace_level_prompt_says_why_it_has_no_link`).
- A list page of trace, span or thread rows carries one `url_template` whose
  slots are filled from the row's own columns, instead of a url per row
  (`row_link_template`, `link_note_for` in
  `src/opik_mcp/read_list/decorations.py`;
  `test_a_project_scoped_page_carries_one_template_for_every_row`). The
  project id comes from the argument, the rows, or the project this call
  resolved, and no lookup is spent on it
  (`test_a_list_scoped_by_name_reads_the_project_off_its_own_rows`).
  Listings of things with no page of their own (score names, online rules,
  metrics) link the page where they appear, with a label that says so
  (`view_link_note`). Case and prompt-version listings link their parent's
  page, which costs one read of the parent.

### schema("list.<entity>")

`schema` answers write operations (see [writes](../writes/design-doc.md)) and
`list.<entity>` keys. The list keys are every OQL vocabulary in
`FILTERABLE_FIELDS` (trace, span, thread, experiment, dataset_item,
dataset_item_case) plus `list.project_metric` (`LIST_SCHEMA_KEYS` in
`src/opik_mcp/read_list/reference.py`). `list_reference` builds the answer
from the tables the validator uses, so the reference and `list` cannot
disagree (`test_reference_matches_the_validator_tables_exactly`). The answer
has the grammar line, each field with its type, operators, key rule, unit,
format and closed values, two examples, the `sdk` default where it applies,
the sortable fields (or why there are none), and whether a window and search
exist (`test_list_trace_reference_carries_fields_operators_sort_window_search`).
An entity with a `reference_fn` (the metric catalog) supplies its own.

## How it works

Call path for `read` and `list`:

```
server.py read / list_entities (FastMCP tool, instrument_tool wrapper)
  -> read_list/read_tool.py run_read  |  read_list/list_tool.py run_list
  -> read_list/registry.py ENTITY_REGISTRY[entity_type]  (an EntityHandler)
  -> read_list/entities/<entity>.py  fetch_fn / list_fn / run_fn / link_fn / page_note_fn
  -> opik_client.py (one client per tool call via client_for_call)
  -> Opik REST API
```

`schema` goes `server.py schema` → `writes/schema_tool.py run_schema`, which
sends `list.*` keys to `read_list/reference.py list_reference`.

Namespaces:

- `src/opik_mcp/read_list/handler.py` defines `EntityHandler`, the one row
  contract every entity fills: fetch, name search, list, row derivation,
  projection, links, page notes, runner, reference, required and optional
  kwargs, window shape, and the `id_only` and `needs_project` flags. It
  imports no entity (`test_the_handler_contract_imports_no_entity`).
- `src/opik_mcp/read_list/registry.py` is only a table plus aliases
  (`test_the_registry_is_a_table_and_not_an_implementation`).
- `src/opik_mcp/read_list/entities/` holds one module or package per entity.
  This doc owns `trace.py`, `span.py`, `thread.py` and `prompt.py`. No entity
  imports another (`test_no_entity_reaches_into_another_entity`).
- The root modules are generic mechanisms. `oql.py` parses and validates
  filters, `sorting.py` sorts, `window.py` resolves windows, `projection.py`
  handles `fields`, `size.py` writes the read header, `slim.py` handles
  child bodies, `paging.py` reads page envelopes and continuations,
  `columns.py` resolves dotted columns, `uri.py` parses URIs, `ui_links.py`
  builds links, `decorations.py` handles optional blocks and link notes,
  `reference.py` builds the list reference, `errors.py` holds
  `EntityArgValidationError`, and `unsupported.py` is the `fetch_fn` of a
  list-only entity.

Boundaries:

- The root must not gain entity names
  ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)). Several
  root modules still name entities in tables (the OQL and sort field tables,
  URI patterns, link areas). These are listed per module under
  `entity_names_at_root` in `tests/ratchets.json`, and that list only shrinks
  (`test_no_new_entity_name_at_the_root`,
  `test_the_entity_name_allowlist_is_current`).
- Every argument error raises `EntityArgValidationError` or a subclass
  (`OQLError` kinds, `SortError`, `WindowError`, `FieldsError`) as the cause of
  the `ToolError`. [analytics](../analytics/design-doc.md) buckets failures by
  that class name, never by the string.
- `decorations.py` (a failed or slow optional block becomes
  `{"error": "Could not load …"}` and the read still returns) and
  `project_names.py` are used by
  [project-overview](../project-overview/design-doc.md). `project_scope.py`
  belongs to [diagnostics](../diagnostics/design-doc.md).
- Credentials, workspace headers and the HTTP client belong to
  [runtime](../runtime/design-doc.md) and [hosted-auth](../hosted-auth/design-doc.md).
  The `instrument_tool` wrapper and the `_*_props` functions in `server.py`
  belong to analytics.

## Decisions

- Five general tools over registries, with reads as tools and no entity
  resources ([ADR 0003](../decisions/0003-five-tool-surface.md);
  `test_no_entity_resources_advertised`).
- Every token on the surface and in an answer has to pay for itself. The
  surface and the instructions have byte ceilings, and every read states its
  size ([ADR 0001](../decisions/0001-context-budget-first.md)).
- The record asked for comes back whole. Children may be slimmed, and every
  cut is stated with a count and the call that gets the rest. Server-side
  compression tiers were removed in #187 because they cut answers the caller
  could not get back ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- `fields=[…]` is the middle layer between a list row and a whole read. It is
  a filter only, and it always declares itself (#197,
  [ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- One OQL grammar, the SDK's `filter_string`, with field and operator tables
  taken from the backend so a string that validates here does not return 400
  (#185; module docstring of `src/opik_mcp/read_list/oql.py`).
- trace, span and thread lists default to `source = "sdk"`, like the Logs
  page, so evaluator, playground and experiment traces do not crowd out
  application traffic. A filter on a parent id turns the default off, because
  the default hid every experiment trace in a drill-in (`PARENT_ID_FIELDS`).
- `search` on a type that lacks it is refused. It used to return an
  unfiltered page under a header that an agent read as the search result
  (`_search_refusal`).
- One copy of each answer: `structured_output=False` everywhere, because the
  structured copy doubled every string answer on the wire (#201,
  `tests/conformance/test_no_duplicate_payload.py`).
- Every read and list page carries a UI link built from the session, and no
  link is better than a wrong one (#201).
- The default project reaches the agent only through the instructions, as a
  name. Tools keep no state (`src/opik_mcp/config.py`).
- Instructions are rendered per session on the HTTP app so they can name the
  OAuth workspace (#151, `install_session_instructions`). stdio keeps the
  text rendered at import.
- The instructions list only tools the server advertises. They once described
  `ask_ollie` when it was not advertised, and the e2e check came with the fix
  (#175).
- Every sentence of an entity's `description` has a probe against the stub
  backend (`tests/e2e/test_description_claims.py`). Tool docstrings are not
  probed there.
- Not built: a size header on `list` answers. The ADR 0001 backlog tracks it.
- Not built: a per-tool result limit through
  `_meta["anthropic/maxResultSizeChars"]`. A very large trace read can exceed
  a host's result cap. This is open in
  [ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md).

## Proven by

- `tests/conformance/test_tool_inventory.py`: the exact tool set, the surface
  byte budget with its history, and the instructions byte budget.
- `tests/conformance/test_schema_snapshots.py`: each tool's input schema
  matches its frozen snapshot.
- `tests/conformance/test_tool_annotations.py`: titles and hints on every
  tool, and Claude Code's description limit (`DESCRIPTION_LIMIT`) on descriptions and instructions, pinned
  as strict expected failures where the text is over.
- `tests/conformance/test_no_duplicate_payload.py`: no output schema, one copy
  per answer.
- `tests/conformance/test_write_tool_surface.py`: `write` and `schema` match
  the operation registry (owned with writes).
- `tests/test_instructions.py`: what the instructions text says, the
  workspace precedence, the default project and date clauses, and that it names
  no unadvertised tool.
- `tests/test_http_auth.py` `test_initialize_names_oauth_workspace`: the HTTP
  app re-renders the instructions per session with the OAuth workspace.
- `tests/e2e/test_stdio_session.py`: a real stdio handshake, the tool surface
  over stdio, a bad argument returns a tool error, and the instructions agree
  with `tools/list`.
- `tests/e2e/test_description_claims.py`: every entity description sentence
  has a claim and a probe, run against `tests/e2e/stub_backend.py`.
- `tests/e2e/test_entity_links_surface.py`: links on reads and pages over a
  real session, built from that session, and no retired address anywhere.
- `tests/test_read_list/test_read_tool.py`: dispatch, URIs, name lookup,
  project scope, windows, error mapping, composite reads, slim children, the
  body budget and continuations.
- `tests/test_read_list/test_list_tool.py`: required and forwarded kwargs,
  table rendering, pagination, size clamp, aliases.
- `tests/test_read_list/test_list_filters.py`: filters, the `sdk` default,
  sort, window, search, header echo, derived columns and empty-page probes.
- `tests/test_read_list/test_oql.py`: the grammar, the backend-aligned field
  and operator tables, and all-at-once error reporting.
- `tests/test_read_list/test_list_schema.py`: `schema("list.<entity>")` matches
  the validator tables.
- `tests/test_read_list/test_fields.py`: projection on read and list is
  declared, keeps the id, and refuses unknown names with the valid ones.
- `tests/test_read_list/test_link_shape.py`: every link has the live
  project-scoped shape and goes through the builder that enforces it.
- `tests/test_read_list/test_ui_links.py`: UI base, workspace precedence,
  link builders.
- `tests/test_read_list/test_uri.py`: URI and pasted-link parsing.
- `tests/test_read_list/test_registry.py`: readable and listable sets, flags,
  required and optional kwargs.
- `tests/test_read_list/test_modular.py`: the root stays generic, and entities
  stay in their own namespaces.

## Log

- 2026-09-24: agent docs, rules and the description-limit check added; titles and hints counted in the surface budget (#202).
- 2026-09-23: every answer carries a UI link; the duplicate `structuredContent` copy removed, one copy per answer (#201).
- 2026-09-22: inline budget on children's bodies, declared in the answer; token estimate uses `_CHARS_PER_TOKEN` (#199).
- 2026-09-21: `fields=[…]` on `read` and `list`, so the caller names what it gets back (#197).
- 2026-09-11: compression tiers removed, since they cut answers the caller could not get back (#187).
- 2026-09-11: read entities moved under `entities/`, one namespace each, so root modules stay generic (#187).
- 2026-09-08: `filters`, `sort`, `since`/`until` and `search` on `list`, so one call answers most questions (#185).
- 2026-09-03: `ask_ollie` and `run_experiment` removed from the surface (#181).
- 2026-09-01: `read_skill` added; the instructions stopped describing a tool that was not advertised (#175).
- 2026-07-24: thread read and list, with project scope and pasted links (#152).
- 2026-06-25: instructions rendered per session, to name the OAuth workspace (#151).
