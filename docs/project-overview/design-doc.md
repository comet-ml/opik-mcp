# project-overview

## Purpose

`read('project')` answers "how is my project doing": the record, the Logs page
figures for a window against the window before it, the names the project
records, and the freshest work in it. `list('project_metric')` charts one
metric over time, optionally grouped, and `list('score_name')` and
`list('online_rule')` enumerate what the overview caps. Open this doc to learn
what a project read fetches, what happens when one of its calls fails, or why
a metric request was refused.

## What it does now

### read('project', id_or_name)

The argument is a project id or a name. Anything that is not a UUID is
looked up with `list_projects(name=…)` (`search_by_name` in
`src/opik_mcp/read_list/entities/project/__init__.py`, called from
`_fetch_with_name_lookup` in `src/opik_mcp/read_list/read_tool.py`):

- One candidate: the read continues with its id
  (`test_read_project_by_name_resolves_to_unique_match`).
- Several candidates: the read fails with `Multiple projects match name
  '<name>'` and lists each candidate's id and name. The lookup matches
  partial names, so `demo` is ambiguous when `demo-2` also exists
  (`test_read_project_by_ambiguous_name_lists_candidates`).
- No candidates, or the lookup call itself fails: the failure is logged at
  debug level and not reported. The read goes on to `GET /projects/{id}`
  with the name as the id, which fails, and the caller gets
  `Not found: project with id '<name>'. Verify the ID is a valid UUID and
  belongs to the current workspace. Detail: …` (`_format_client_error` in
  `read_tool.py`; the message shape is pinned for an id by
  `test_read_surfaces_not_found_with_hint`).

Unverified: no test covers the no-match or failed-lookup path for a name.

The read accepts
`since` and `until`, which set the summary window
(`HANDLER.read_window` in `src/opik_mcp/read_list/entities/project/__init__.py`).

The answer is one JSON object with these keys:

- `project`: the record from `GET /projects/{id}`.
- `summary`: the Logs page figures (below).
- `vocabulary`: the names the project records (below). Left out when the
  project has none of them.
- `contains`: the freshest item of each kind (below). Left out for a project
  with no activity.
- `url`: the project's Logs page, when the UI base is configured
  (`test_read_project_carries_a_link_to_its_page`,
  `test_read_project_omits_the_link_when_opik_url_is_unconfigured`).

The record is fetched first and alone. If it fails (bad id, another
workspace), the read fails with a tool error and nothing else is called
(`test_read_project_fails_when_the_project_record_fails`). After that, six
calls run concurrently on the one connection the tool call owns: the summary,
score names, usage keys, online rules, experiment metadata keys and the
activity feed (`fetch_project` in
`src/opik_mcp/read_list/entities/project/read.py`,
`test_read_project_gathers_its_calls_concurrently`).

### The summary

`summary` reproduces the four cards on the Logs page with one call,
`POST /projects/{id}/kpi-cards` (`trace_summary` in
`src/opik_mcp/read_list/entities/project/summary.py`). It has:

- `window`: `since`, `until`, `days` when the span is a whole number of days,
  and `compared_to`, the previous period of the same length ending at
  `since` (`window` in `summary.py`,
  `test_read_project_says_what_previous_means`,
  `test_read_project_reports_a_partial_day_window_without_a_day_count`).
- `source`: always `"sdk"`. The request carries the Logs page's own
  `source = "sdk"` filter, so experiment and playground traffic is excluded
  and the numbers match the screen
  (`test_read_project_counts_only_sdk_traffic_and_says_so`,
  `test_the_summary_asks_for_sdk_traffic_as_a_json_string`).
- `traces`: `count`, `errors`, `avg_duration` and `total_cost`, each as
  `{current, previous}`, keyed by the backend's `type` field and never by
  position (`test_read_project_maps_figures_by_type_not_by_position`).
- `note`: present when the current window has no SDK traces.

With no `since`/`until`, the window is the last `WINDOW_DAYS` days
against the same span before it
(`test_read_project_covers_the_last_seven_days_against_the_previous_seven`).
The UI's Logs cards open on 30 days, so the two differ by default. The
`initialize` instructions tell the agent that `since="30d"` matches the UI
(`src/opik_mcp/instructions.py`), and the read states the window it used
(`test_read_project_accepts_a_relative_window`). Relative and absolute bounds
are both accepted. One clock reading resolves both bounds. An inverted window
or one of zero length is refused before the backend is called
(`test_read_project_accepts_absolute_instants`,
`test_read_project_measures_a_relative_window_from_one_clock_reading`,
`test_read_project_rejects_an_inverted_window_before_the_backend`,
`test_read_project_rejects_a_window_of_no_length`).

For a period with no traces, the backend sends a zero error rate and a null
average duration. The summary reports both as `null` for that period and
keeps `count` and `total_cost` at zero, since a count and a sum over nothing
are really zero. Each period is judged on its own count
(`shape_stats` in `summary.py`,
`test_read_project_reports_a_rate_over_no_traces_as_undefined`,
`test_read_project_keeps_a_real_zero_error_rate`).

Floats are rounded to four decimals; values below 1e-4 keep three significant
digits so a small cost does not print as zero (`_readable` in `summary.py`).

The figures carry no unit. `errors` is a percent and `avg_duration` is in
milliseconds, which a reader cannot tell from the number
(`.claude/dogfood/memory/project-summary-numbers-have-no-unit.md`, open).

If the kpi-cards call fails, `summary` keeps `window` and `source` and has
`error` where `traces` would be. There is no `traces` key and no zeros:

```
"summary": {
  "window": {"since": "…", "until": "…", "days": …, "compared_to": {…}},
  "source": "sdk",
  "error": "Could not load this project's metrics: <detail>. Retry, or count directly with list('trace', project_id='<id>', since='<window since>')."
}
```

`<detail>` is the exception's message, or its class name when the message is
empty (`describe` in `src/opik_mcp/read_list/decorations.py`). The suggested
call uses the resolved project id and the window's start. The rest of the
read still arrives (`trace_summary` in `summary.py`,
`test_read_project_says_the_metrics_failed_rather_than_reporting_zeros`,
`test_a_decoration_that_times_out_does_not_take_the_read_with_it`). The
summary catches the same errors as the other parts but has no deadline, so a
slow kpi-cards call is waited for
(`test_the_summary_is_not_on_a_decoration_deadline`).

### The vocabulary

`vocabulary` holds up to four parts, each omitted when empty
(`assemble` in `src/opik_mcp/read_list/entities/project/vocabulary.py`):

- `score_names`: feedback score names, capped at `SCORE_NAMES_CAP` from
  `src/opik_mcp/read_list/project_names.py`.
- `usage_keys`: every token usage key, uncapped, because no call lists them
  on their own (`test_every_usage_key_is_listed`).
- `online_rules`: automation rule names, one page of `RULES_CAP`.
- `experiment_metadata_keys`: top-level `metadata` keys from the freshest
  `METADATA_SAMPLE` experiments, most common first, with `sampled_from` and
  a `filter` example for `list('experiment')`
  (`test_read_project_names_the_metadata_keys_its_experiments_carry`,
  `test_the_metadata_keys_are_the_projects_and_one_page_of_it`).

Every part has `names` and `total`, including when nothing was cut
(`test_read_project_reports_a_total_even_when_nothing_was_cut`). A capped
part adds `all`, the call that returns the rest: `list('score_name', …)` or
`list('online_rule', …)`. When the backend gives no total and the list fills
the cap, `all` is still added
(`test_read_project_caps_a_long_score_list_and_says_how_many_there_are`,
`test_a_rules_page_without_a_total_keeps_its_pointer`).

### What the project contains

`contains` comes from one page of the project's activity feed (`FEED_PAGE`,
the backend's maximum) and keeps the newest entry of each kind as
`{name, id, at}`, with `at` as a date (`distil` in
`src/opik_mcp/read_list/entities/project/contents.py`,
`test_read_project_names_the_freshest_thing_of_each_kind`). Kinds include
experiments, dataset versions, prompt versions and optimization runs. The
per-day trace roll-up is dropped because its `name` field holds a count
(`test_read_project_never_renders_the_daily_trace_count_as_a_name`). Fields
the backend omits are not rendered as null
(`test_read_project_does_not_invent_the_fields_the_backend_omits`). When the
feed has more records than the page, `note` says a missing kind may be
older than the page
(`test_read_project_says_when_older_kinds_may_be_off_the_page`).

An experiment entry is opened with `read('experiment', id)`. An optimization
entry has no readable entity, so it gets a UI `url` (`UI_PAGE` in
`contents.py`, `project_links` in `read.py`). Other kinds get no link,
because their UI route needs an id the feed does not carry.

### When one part fails

Each vocabulary part and `contains` runs inside `block` from
`src/opik_mcp/read_list/decorations.py`, which is shared with other entities
(see [tool-surface](../tool-surface/design-doc.md)). `block` catches
`BLOCK_ERRORS`: the client's typed auth, not-found, validation and server
errors, and every `httpx.HTTPError` (timeouts, resets, DNS, TLS). It also puts
each part on a deadline of `DEADLINE_SECONDS`. A caught error becomes
`{"error": "Could not load <part>: <detail>"}`, and a missed deadline becomes
`{"error": "Could not load <part>: the backend took longer than <n>s, so it
was left out … Retry, or ask for it on its own."}`, in that part's place.
Anything else propagates and fails the read. The other parts and the
summary still answer
(`test_one_failing_part_does_not_take_the_read_down`,
`test_read_project_reports_a_failed_vocabulary_part_without_losing_the_rest`,
`test_a_decoration_that_times_out_does_not_take_the_read_with_it`,
`test_a_slow_decoration_does_not_hold_up_the_answer`,
`test_read_project_reports_a_failed_activity_call_explicitly`). An error with
an empty message falls back to the exception's class name
(`test_an_error_with_no_message_still_says_something`).

### list('project')

Rows carry `created_at` and `last_updated_trace_at`, so an agent can pick the
project with live traffic from one page (`list_extra_fields` in
`src/opik_mcp/read_list/entities/project/__init__.py`).

### list('project_metric', …)

Arguments: `project_id` or `project_name` (required), `metric_type`
(required), `interval`, `since`, `until`, `filters`, `breakdown`, `series`.
The answer is a header line and a pipe table with one row per time bucket
(`run_project_metric` in
`src/opik_mcp/read_list/entities/project_metric/runner.py`).

The header echoes what applied: metric, interval (marked "from the window"
when defaulted), the window, the compiled filter and the grouping with its
series (`test_the_first_line_echoes_metric_interval_window_and_source`).

`schema("list.project_metric")` returns the metric table with each metric's
entity and unit, the grouping matrix, the interval rule and the series rules,
without calling the backend (`reference` in
`src/opik_mcp/read_list/entities/project_metric/reference.py`,
`test_the_schema_reference_answers_without_touching_the_backend`).

Metrics are named `trace_*`, `span_*`, `thread_*` and
`guardrails_failed_count`. The backend's bare `DURATION` and `COST` are
exposed as `trace_duration` and `trace_cost` (`METRICS` in
`src/opik_mcp/read_list/entities/project_metric/catalog.py`).

Window and interval:

- The window defaults to the last `DEFAULT_WINDOW_DAYS` days
  (`test_the_window_defaults_to_the_last_seven_days`).
- With no `interval`, it follows the window as the UI's Metrics tab does:
  hourly up to 3 days, daily up to 30, weekly beyond
  (`interval_for_window`, `test_the_interval_is_the_one_the_ui_would_pick`).
- An explicit interval is sent as given, however many rows it makes; there is
  no bucket cap (`test_an_explicit_interval_is_taken_as_given_whatever_the_window`,
  `test_a_wide_hourly_request_reaches_the_backend`).
- `total` returns one row labelled with the window's span
  (`test_a_total_row_is_labelled_with_the_window_not_its_first_day`).

Filters:

- OQL, compiled against the fields of the metric's entity and sent in that
  entity's filter array (`trace_filters`, `span_filters` or
  `thread_filters`) (`request_body` in `catalog.py`,
  `test_a_span_metric_is_filtered_by_span_fields_in_the_span_array`).
- Trace and span metrics get `source = "sdk"` unless the filter names a
  source or a parent record
  (`test_an_explicit_source_is_not_overridden`,
  `test_a_drill_in_on_an_experiment_gets_no_sdk_default`).
- Thread metrics get no source default, and a thread filter on `source` or
  `environment` is refused, because the backend drops those fields for
  threads without an error
  (`refuse_dropped_fields`,
  `test_a_thread_metric_does_not_claim_an_sdk_filter_it_cannot_apply`,
  `test_asking_a_thread_metric_for_source_is_refused_not_ignored`).

Grouping (`breakdown`):

- Values: `tags`, `name`, `error_info`, `error_type`, `model`, `provider`,
  `span_type`, `guardrail_name`, and `metadata.<key>` with the key inline
  (`BREAKDOWNS`, `parse_breakdown`,
  `test_grouping_by_a_metadata_key_takes_the_key_inline`).
- Which metric takes which grouping is transcribed from the backend's
  `BreakdownField`. `trace_average_duration`, `trace_error_rate`,
  `span_average_duration`, `span_error_rate`, `span_cost`,
  `thread_average_duration` and `thread_cost` take none, because the backend
  omits them from its sets
  (`test_the_ungroupable_metrics_are_exactly_the_ones_the_backend_omits`).
- A refused grouping lists what the metric does accept and names a metric of
  the same family that takes the grouping, or says none does
  (`test_a_refusal_points_at_a_metric_that_answers_the_same_question`,
  `test_a_grouping_no_metric_of_that_kind_supports_says_so`).
- The backend returns at most `BACKEND_SERIES_CAP` groups plus
  `__others__`. `__others__` is summed per bucket for counts, costs, tokens
  and guardrail failures, and left blank for the rest
  (`test_others_is_summed_per_bucket_for_a_count`,
  `test_others_is_left_blank_where_a_sum_would_be_nonsense`).
- A group with an empty key is labelled `(no value)`, and a group name with
  a pipe stays one column
  (`test_a_group_with_no_key_is_not_labelled_with_the_metric_name`,
  `test_a_group_name_with_a_pipe_stays_one_column`).

`series` picks one series of a multi-series metric when it is grouped:

- Duration metrics chart one percentile, `p50` by default.
- Token metrics chart one usage key, `total_tokens` by default.
- Feedback-score metrics have no default and require `series=<score name>`.
- `series` on an ungrouped metric, or on a single-series metric, is refused.
  Ungrouped, every series comes back as its own column
  (`resolve_series` in `catalog.py`,
  `test_a_grouped_duration_defaults_to_the_median_and_says_which`,
  `test_a_grouped_token_metric_defaults_to_the_total`,
  `test_a_grouped_score_metric_asks_which_score_rather_than_choosing`,
  `test_series_without_a_breakdown_is_refused_not_ignored`,
  `test_a_metric_that_fans_out_per_score_name_returns_every_series`).
- When a grouped answer comes back empty, the chosen score name or usage key
  is checked against the project's recorded names. An unknown name is
  refused with the names that exist; a known one gets a note that the window
  is empty. An answer with data triggers no lookup, and a failed lookup
  refuses nothing (`check_series` in `runner.py`,
  `test_an_unrecorded_score_name_lists_the_ones_that_exist`,
  `test_a_recorded_name_with_no_data_says_the_window_is_empty`,
  `test_an_answer_with_data_is_not_charged_a_name_lookup`,
  `test_a_name_lookup_that_fails_does_not_refuse_the_name`).

Rendering (`render` in
`src/opik_mcp/read_list/entities/project_metric/table.py`):

- Rows are the union of timestamps across series, and each cell is looked up
  by its timestamp, because grouped series are not filled
  (`test_a_group_that_ran_on_one_day_is_charted_on_that_day`).
- For error rates and average durations, a companion count is fetched
  concurrently. Buckets with no traces, spans or threads are left out and
  counted under the table. If the count fails, the chart still renders
  without that note (`companion_count` in `catalog.py`,
  `test_a_bucket_with_no_traces_is_left_out_rather_than_charted_as_zero`,
  `test_the_chart_survives_the_companion_count_failing`,
  `test_a_count_metric_is_not_charged_a_second_call`).
- Buckets the backend reports as null are left out and counted; zeros stay
  (`test_a_zero_is_kept_where_a_null_is_dropped`).
- An all-zero series collapses to one line
  (`test_an_all_zero_series_collapses_to_one_line`).
- Integers print without a decimal, and small values keep three significant
  digits (`test_integers_are_not_printed_as_floats`,
  `test_a_sub_cent_cost_is_not_rounded_to_zero`).
- Daily buckets are labelled with the date, hourly ones with the hour
  (`test_hourly_buckets_are_labelled_with_the_hour`).

Refused before any backend call: an unknown or missing `metric_type`, an
unknown `interval`, an unknown filter field, an incompatible grouping, and
`page`, `size`, `sort` or `fields`, since rows are time buckets
(`test_the_refusals_never_reach_the_backend`,
`test_collection_arguments_are_refused_not_ignored`). On a timeout the list
tool returns the handler's `run_timeout_hint`: narrow the window or widen the
interval.

### list('score_name') and list('online_rule')

Both need `project_id` or `project_name`
(`src/opik_mcp/read_list/entities/score_name.py`,
`src/opik_mcp/read_list/entities/online_rule.py`).

- `score_name` rows have no id. The endpoint returns all names with no
  paging, so the server slices the page itself and reports the true total.
  A footer says the names mix trace, span and thread scores
  (`test_a_score_name_page_is_cut_here_and_says_so`,
  `test_the_name_lists_answer_under_a_project`).
- `online_rule` rows add `type`, `enabled` and `sampling_rate`.

Neither supports `read`.

## How it works

Read path: `read` in `server.py` calls `read_list/read_tool.py`, which finds
the `project` handler in `read_list/registry.py` and calls `fetch_project` in
`src/opik_mcp/read_list/entities/project/read.py`. It calls
`trace_summary` (`summary.py`), the four vocabulary loaders
(`vocabulary.py`) and `project_contents` (`contents.py`), then the read tool
merges `project_links` into the answer and strips the private `_project_id`
key.

List path: `list_tool.py` hands `project_metric` to the handler's `run_fn`,
`run_project_metric`, which skips the collection machinery. The runner
validates with `catalog.py`, resolves the project with `require_project_id`
from `src/opik_mcp/read_list/project_scope.py` (owned by
[diagnostics](../diagnostics/design-doc.md)), calls
`get_project_metrics` once or twice, and renders with `table.py`.
`schema("list.project_metric")` reaches `reference.py` through the handler's
`reference_fn`. `score_name` and `online_rule` are ordinary `list_fn`
handlers scoped by `scope_of`.

Shared modules this feature uses but does not own, all described in
[tool-surface](../tool-surface/design-doc.md):

- `src/opik_mcp/read_list/decorations.py`: `block`, `BLOCK_ERRORS`,
  `DEADLINE_SECONDS`, `describe`.
- `src/opik_mcp/read_list/project_names.py`: score names and usage keys,
  shared by the vocabulary and the series check so neither entity imports the
  other ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)).
- `src/opik_mcp/read_list/window.py` for bounds, and
  `src/opik_mcp/read_list/oql.py` for `compile_filters` and
  `SDK_SOURCE_CLAUSE`.

Backend calls go through `OpikReadClient` in `src/opik_mcp/opik_client.py`
(see [runtime](../runtime/design-doc.md)): `get_project`,
`get_project_kpi_cards`, `list_project_score_names`,
`list_project_token_usage_names`, `list_automation_rules`,
`list_experiments`, `list_project_activities`, `get_project_metrics`.

Namespaces owned: `src/opik_mcp/read_list/entities/project/`,
`src/opik_mcp/read_list/entities/project_metric/`,
`src/opik_mcp/read_list/entities/score_name.py`,
`src/opik_mcp/read_list/entities/online_rule.py`.

## Decisions

- The summary reproduces the Logs page cards from `kpi-cards` instead of
  computing figures from raw traces, so the agent's numbers match the screen
  (#187).
- The summary is SDK-only and cannot be changed. A summary that disagrees
  with the screen was judged worse than none (`SDK_SOURCE_FILTER` in
  `summary.py`, #187).
- The default window is `WINDOW_DAYS` days, shorter than the UI's 30 days. The read
  states its window and `compared_to`, and the instructions give `since="30d"`
  for the UI's view (#187).
- A rate or an average over no samples is `null`, and a failed load is an
  `error`. Neither is shown as zero, because an agent may act on "0% errors"
  ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- The record is required and fails the read. Every other part fails in place,
  so one slow or broken endpoint does not cost the whole answer. The summary
  has no deadline: its figures are what the caller asked for, and a timeout
  there would leave nothing to report (#187).
- Capped lists state their total and name the call for the rest. Usage keys
  are not capped because no call would return the rest
  ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- Experiment metadata keys are sampled from recent runs because no endpoint
  lists them, and the sample size is stated (#192).
- Grouping rules are copied from the backend's `BreakdownField` and applied
  locally, because the backend's refusals name the wrong reason. Every
  refusal that needs no call is made before the backend is reached, since the
  backend's errors do not name the field (#187).
- The metric reference lives in `schema("list.project_metric")`, off the
  advertised surface, so only a caller about to chart pays for it
  ([ADR 0001](../decisions/0001-context-budget-first.md)).
- No cap on buckets or series columns: the interval default keeps answers
  small, and an explicit wide request is the caller's choice (#187).
- `fields` is refused on `list('project_metric')` because a bucket has no
  record to project (#197).
- Not built: units on the summary figures. Found by `/dogfood` and kept as
  backlog (`.claude/dogfood/memory/project-summary-numbers-have-no-unit.md`).
- Not built: links for `contains` entries other than optimization runs,
  because their UI routes need an id the feed does not carry (#201).

## Proven by

- `tests/test_read_list/test_read_tool.py`: the project read, summary window,
  undefined rates, failures in place, deadlines, vocabulary caps and totals,
  `contains`, links, and name resolution (the `test_read_project_*` tests and
  the decoration tests next to them).
- `tests/test_read_list/test_project_metrics.py`: every `project_metric`
  rule: validation, window and interval, filters, grouping, series,
  companion count, rendering.
- `tests/e2e/test_project_overview_surface.py`: the same features over stdio
  against a stub backend on a socket, checking both the answer and the
  request the backend received.
- `tests/test_opik_client_read.py`: `test_score_names_sends_the_project_id_as_a_json_array`
  and `test_automation_rules_keeps_the_trailing_slash` for the two name
  endpoints.

## Log

- 2026-09-23: list pages of all four entities carry a UI link note; optimization links use the shared URL builder (#201).
- 2026-09-21: `list('project_metric')` refuses `fields=[…]` (#197).
- 2026-09-21: grouped column names escaped so a pipe in a group name stays one column (#195).
- 2026-09-21: vocabulary adds `experiment_metadata_keys`, sampled from recent experiments (#192).
- 2026-09-17: `list('project_metric')` gets a timeout hint and drops collection arguments it does not use (#190).
- 2026-09-11: `read('project')` gives summary, vocabulary and contents; `list('project_metric')`, `list('score_name')` and `list('online_rule')` added (#187).
