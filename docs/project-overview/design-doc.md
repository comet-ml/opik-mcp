# project-overview

## Purpose

`read('project')` answers "how is my project doing"; `list('project_metric')`
charts one metric over time. This doc answers what a project read returns,
what happens when one of its calls fails, and why a metric request was refused.

## What it does now

### read('project', id_or_name, since=…, until=…)

A name is looked up as in [tool-surface](../tool-surface/design-doc.md#read).
For a project, `search_by_name` asks for 5 candidates, and more than one is
refused even when one matches exactly
(`test_read_project_by_ambiguous_name_lists_candidates`). No match, or a failed
lookup, ends in the not-found error for the name. Unverified: the backend
matches substrings (the test uses a fake client).

The answer is one JSON object:

- `project`: the record. If it fails, the read fails and no other call is made
  (`test_read_project_fails_when_the_project_record_fails`).
- `summary`: the Logs page's four cards from `POST /projects/{id}/kpi-cards`,
  as `traces` with `count`, `errors`, `avg_duration` and `total_cost`, each
  `{current, previous}`. It has `window` with `compared_to` (the period of the
  same length before it), `source: "sdk"`, and `note` when the window has no
  SDK traces. `since`/`until` set the window, which defaults to the last
  `WINDOW_DAYS` days. An inverted or empty window is refused before any call.
- `vocabulary`: `score_names`, `usage_keys`, `online_rules` and
  `experiment_metadata_keys`, each with `names` and `total`. A capped part adds
  `all`, the list call for the rest. Metadata keys come from recent
  experiments and state `sampled_from`. Empty parts, and an empty block, are
  left out.
- `contains`: the newest activity-feed entry of each kind as `{name, id, at}`.
  An optimization run also gets a UI `url`. `note` says when older kinds may be
  past the one page read.
- `url`: the Logs page, when the UI base is configured.

When a part fails, that part becomes `{"error": "Could not load …"}` and the
rest still arrive. A failed summary keeps `window` and `source`, has `error` in
place of `traces`, and names a `list('trace', …)` call that counts by hand.
The other parts run on a deadline through `decorations.block`
([tool-surface](../tool-surface/design-doc.md#how-it-works)). The summary has
none, since its figures are the answer (#187). Only the client timeout bounds a
hanging kpi-cards call ([runtime](../runtime/design-doc.md#timeouts-and-connections)).

### list('project_metric', …)

- Required: `project_id` or `project_name`, and `metric_type`. Optional:
  `interval`, `since`, `until`, `filters` (OQL on the metric's entity),
  `breakdown`, `series`.
- The answer is a header line echoing what applied, then one row per time
  bucket. A defaulted interval is marked "from the window" and matches the
  UI's Metrics tab (`interval_for_window`). An explicit interval is used as
  given, with no bucket cap.
- Trace and span metrics get `source = "sdk"` unless the filter names a source
  or a parent record. Thread metrics get no default.
- `schema("list.project_metric")` answers which metrics, units, groupings and
  series exist, without a backend call.
- Grouped, a duration charts `p50` and a token metric `total_tokens` unless
  `series` says otherwise. A grouped feedback-score metric requires `series`.
  An empty grouped answer checks that name against the project, and an unknown
  one is refused with the names that exist.
- Rates and averages fetch a companion count, so an empty bucket is left out
  and counted instead of charted as zero. Null buckets are also left out;
  zeros stay.

Refused before any backend call, each with the valid options: an unknown or
missing metric, an unknown interval, an unknown filter field, a thread filter
on `source` or `environment`, a grouping the metric does not take (the refusal
names a metric that does), a bad or missing `series`, and `page`, `size`,
`sort` or `fields`.

### Other lists

- `list('project')` rows add `created_at` and `last_updated_trace_at`, so one
  page shows which project has live traffic.
- `list('score_name')` and `list('online_rule')` need project scope and have no
  `read`. The score-name endpoint returns every name at once, so the server
  slices the page and reports the true total. Rule rows add `type`, `enabled`
  and `sampling_rate`.
- All four list types carry an "Open in Opik" page note, built by
  `page_note_of` from each handler's `row_link_template` or `view_page`.

## How it works

```
read  → read_tool._fetch_with_name_lookup → project.read.fetch_project
        → get_project, then gather(summary, 4 vocabulary loaders, contents)
        → project_links (read_tool attaches, strips _project_id)
list  → list_tool → project_metric HANDLER.run_fn = runner.run_project_metric
        → catalog (validate) → require_project_id → get_project_metrics (+ count) → table.render
```

Where to start:

- Summary figures and window: `src/opik_mcp/read_list/entities/project/summary.py`.
- A new vocabulary part: `src/opik_mcp/read_list/entities/project/vocabulary.py`,
  then the gather in `read.py`. The entity description in
  `src/opik_mcp/read_list/entities/project/__init__.py` has a probe per
  sentence in `tests/e2e/test_description_claims.py`.
- A metric, grouping, series or refusal: `src/opik_mcp/read_list/entities/project_metric/catalog.py`;
  the order of checks is in `run_project_metric`, rendering in `table.py`.
- Test fakes: `_vocab_fake` in `tests/read_list/test_read_tool.py`.
- Not here: `decorations.py`, `project_names.py`, `window.py` and `oql.py` are
  [tool-surface](../tool-surface/design-doc.md)'s, `project_scope.py` is
  [diagnostics](../diagnostics/design-doc.md)'s, the client is [runtime](../runtime/design-doc.md)'s.

## Decisions

- The summary copies the Logs page cards, including their SDK-only filter,
  instead of computing its own figures. A summary that disagrees with the
  screen was judged worse than none, so the filter has no switch
  (`SDK_SOURCE_FILTER`, #187).
- The default window is `WINDOW_DAYS` days, shorter than the UI's 30, and the
  instructions give `since="30d"` for the UI's view (#187). The cards and the
  summary share the SDK filter, so a count that differs from the cards comes
  from the window. Unverified: the Logs trace table also filters to SDK (the
  `summary.py` docstring says so; no test here checks the UI).
- A rate or average over no traces is `null` (the backend sends a zero error rate), and a
  failed part is an `error`. Neither shows as `0`, because an agent may act on
  "0% errors" ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- Usage keys and metadata keys have no cap and no `all`, because no call lists
  the rest. Metadata keys are sampled because no endpoint lists them (#192).
- Grouping rules are copied from the backend's `BreakdownField` and checked
  locally, because the backend's refusals name the wrong reason (#187).
- Not built: units on summary figures (`errors` is a percent, `avg_duration`
  milliseconds). Backlog note at the path
  `.claude/dogfood/memory/project-summary-numbers-have-no-unit.md`.
- Not built: links for `contains` kinds other than optimization runs, because
  their UI routes need an id the feed does not carry (#201).

### Traps

- kpi-cards returns its stats in the backend's order, so the summary maps them
  by `type` (`test_read_project_maps_figures_by_type_not_by_position`).
- The activity feed's per-day trace entry holds a count in its `name` field and
  is dropped (`test_read_project_never_renders_the_daily_trace_count_as_a_name`).
- The backend silently ignores `source` and `environment` on thread metrics,
  so they are refused (`refuse_dropped_fields`).
- Grouped series are not filled per bucket, so cells are keyed by timestamp
  (`test_a_group_that_ran_on_one_day_is_charted_on_that_day`).

## Proven by

- `tests/read_list/test_read_tool.py`, the `test_read_project_*` tests and
  the decoration tests next to them: record, summary, vocabulary, `contains`,
  links, name resolution, failures in place.
  `test_the_summary_is_not_on_a_decoration_deadline` guards the missing deadline.
- `tests/read_list/test_project_metrics.py`: every `project_metric` rule.
- `test_the_ungroupable_metrics_are_exactly_the_ones_the_backend_omits`: the
  copied grouping matrix.
- `tests/e2e/test_project_overview_surface.py`: the same over stdio against a
  stub backend. `tests/client/test_read.py`: the name endpoints' wire shapes.

## Log

- 2026-09-23: list pages of all four entities carry a UI link note; optimization links use the shared URL builder (#201).
- 2026-09-21: vocabulary adds `experiment_metadata_keys`, sampled from recent experiments, to filter experiments on (#192).
- 2026-09-11: `read('project')` overview and `list('project_metric')`, `score_name`, `online_rule`, for "how is it doing" (#187).
