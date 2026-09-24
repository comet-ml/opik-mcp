# experiment-flows

## Purpose

This feature covers experiments, datasets and their cases. It lists and reads
them, compares two or more experiments case by case, finds one case in a
large dataset, and provides the writes an evaluation needs. Open it to learn
what `list('dataset_item', experiment_ids=[…])` puts on the page and why, how
a case is found, or what `dataset.create` and the other evaluation writes
send to the backend.

## What it does now

### Experiments

`list('experiment')` returns one row per run, newest first. The backend
orders experiments by id descending, and the ids are time ordered
(`HANDLER.no_window_reason` in `src/opik_mcp/read_list/entities/experiment.py`).
The call takes `filters` and `sort` but refuses `since`/`until`, because the
backend has no time window for experiments. The refusal tells the caller to
page through the newest-first list instead.

Every row carries the same core columns: `type`, `status`, `dataset_name`,
`created_at`, `trace_count` and `feedback_scores` (`_SPINE`). `trace_count`
sits next to the scores, so a mean computed over very few cases is visible
beside the case count. The columns in `_CONDITIONAL` (assertion runs, cost,
p50 and p90 duration, prompt version, dataset version, optimization id, `url`)
appear only when at least one row on the page has a value, and the note
under the table names the ones it left out (`project_experiments`). The note
also lists every filterable field, taken from the OQL compiler's own table,
and points at `schema("list.experiment")` for the operators.

`derive_columns` fills cells the record carries in a split or nested
form. `assertion_runs` is `passed/total` and stays empty for a run without
assertions, so no zero looks like a failure. `prompt_version` lists every
version the run used, or the short commit for a version with no number.
`total_estimated_cost` is rounded to six significant digits.

Each row links to the UI compare view for that run. The address needs the
run's project, dataset and id, so a row that lacks any of them gets no link
(`experiment_links`, `row_link`). A projected row always keeps
`prompt_version` and `url` (`list_identity_fields`).

Notes on an experiment page (`page_note`):

- On an empty page past the last page, the note says so and gives the total.
- On an empty page from a query that matched nothing, one call fetches the
  workspace count and the note says "none match", plus the accepted values
  if the caller filtered. An empty workspace gets no note, and a failed
  lookup drops the note, never the page.
- On a page sorted by a score (`feedback_scores.*`, `experiment_scores.*`,
  `pass_rate`) where either of the top two rows covers fewer than
  `THIN_SAMPLE` cases (`src/opik_mcp/read_list/sample.py`), a line gives the
  gap and both counts. It then points at the per-case comparison
  (`_ranking_caveat`).

`read('experiment', id)` returns the whole record. When the run has a
dataset, the record also carries `comparePerCase`, the exact
`list('dataset_item', experiment_ids=[…])` call to use next
(`_with_next_step`).

### Datasets and test suites

A test suite is a dataset whose experiments carry
`evaluation_method = evaluation_suite`. It is the same record and the same
entity (`HANDLER.description` in
`src/opik_mcp/read_list/entities/dataset/__init__.py`). The older names
for the two entities still resolve to `dataset` and `dataset_item`
(`ENTITY_ALIASES` in `src/opik_mcp/read_list/registry.py`).

`read('dataset', id_or_name)` returns the dataset record without its items.
`list('dataset')` is workspace-wide with a name substring and adds
`created_at`. The link points at the dataset page under its project. A
dataset created at workspace level has no project and no UI page anywhere,
and the answer says so (`dataset_links`).

### Finding a case: `list('dataset_item', dataset_id=…)`

Without `experiment_ids`, `list('dataset_item')` lists the dataset's own
cases. `dataset_id` is required (`list_items`).

- Columns are the keys of the items' `data` maps, read from the page. The
  keys the SDKs document (`input`, `expected_output`, `output`, `context`,
  `reference`) come first. The others follow, most-filled first, then by
  name. The count is capped at `_MAX_DATA_COLUMNS`, and the note names the
  omitted keys (`data_columns`, `project_items` in
  `src/opik_mcp/read_list/entities/dataset/items.py`).
- Each cell is cut to a share of a fixed page budget (`_PAGE_DATA_BUDGET`)
  divided by rows times columns, between a floor and a ceiling
  (`cell_limit`). Fewer rows or fewer columns leave more room for each
  value. The cut is declared, with the hint to use a smaller `size` or
  `read('dataset_item', id)`.
- `filters` uses the case vocabulary `dataset_item_case`: `id`,
  `data.<key>`, `full_data` (the whole payload without naming a key), `tags`,
  `source`, `trace_id`, `span_id`, the timestamps and the authors
  (`FILTERABLE_FIELDS["dataset_item_case"]` in
  `src/opik_mcp/read_list/oql.py`). Filtering on `trace_id` finds the case
  that was built from a given trace.
- A filter on a field that only the comparison has (`duration`, `output`,
  `comments`, `feedback_scores`) is refused, and the refusal names the
  `experiment_ids` call where that field applies. Cost and usage fields are
  refused on both calls, because the compare endpoint accepts them and then
  ignores them (`IGNORED_BY_BACKEND`).
- Every `sort` is refused, because the items endpoint has no sorting
  parameter. The refusal says that only the comparison orders cases
  (`UNSORTABLE_WHY` in `src/opik_mcp/read_list/sorting.py`).
- A projected row keeps `trace_id` when the case has one
  (`ITEM_HANDLER.list_identity_fields`).

`read('dataset_item', id)` returns one case uncut. The item id is enough and
the dataset is not needed (`fetch_item`).

### Comparing experiments: `list('dataset_item', experiment_ids=[A, B])`

When `experiment_ids` is present, the whole call goes to `run_compare` in
`src/opik_mcp/read_list/entities/dataset/compare.py`. The answer is one row
per case, with each experiment's run joined on. The backend does the join,
so a page costs the same whatever the dataset's size
(`test_a_comparison_costs_the_same_on_twenty_and_on_a_hundred_thousand_cases`).

#### What the call refuses before it fetches

`_validated_ids`, `_refuse_window`, `_dataset_of` and `_resolve` refuse:

- an empty `experiment_ids`, ids that are not strings, repeated ids, and
  more than `MAX_EXPERIMENTS`;
- `since`/`until`, because a case belongs to a dataset, which has no time
  window;
- experiments of different datasets, naming each run and its dataset;
- an experiment with no dataset;
- a `dataset_id` other than the one the experiments ran (the dataset
  comes from the experiments, so it is never needed);
- a filter on the runs with `size` above `REFETCH_ROW_CAP` (see below);
- a sort field the joined endpoint does not order by. The backend would
  answer 200 with an unsorted page (`_sorting`, `_DATASET_ITEM_SORTABLE`).

The first id is the baseline. The experiments are labelled `E1`, `E2`, … in
the order given, and the header shows the legend with names and ids
(`_legend`).

#### Warnings above the table

`_guards` prints these before the table, and says nothing about a field a
record lacks:

- The runs used different dataset versions, so a dash may mean the case did
  not exist yet. The call still runs
  (`test_runs_over_different_dataset_versions_are_compared_with_a_warning`).
- A run is still running, so its scores will change.
- The runs covered different numbers of cases. If the smallest count is
  thin (`is_thin`), the warning says it is too few to compare.

#### The table

Layout comes from `render` in
`src/opik_mcp/read_list/entities/dataset/layout.py`. Columns are `id`, up
to `MAX_DATA_COLUMNS` case keys, up to `MAX_SCORE_COLUMNS` scores ranked by
how many rows carry them, then `worst_trace`. Runs from a test suite also
get `passed` and `reason`. The experiments share one cell per score,
separated by ` / ` in legend order. Omitted keys and scores are named under
the table.

A score cell is one of three kinds, decided once per row (`ComparedRow._kind`):

- A number is the mean over that experiment's runs of the case. With
  exactly two experiments the cell adds `Δ`, E2 minus E1 with its sign.
- A categorical score shows its labels and is never averaged or subtracted.
  Labels come from the feedback definitions, fetched once per call
  (`list_feedback_definitions`, `ScoreKinds`), so a bare number gets its
  label back. Unreadable definitions leave every score a number.
- A score written by two authors (a judge through the SDK and a reviewer in
  the UI) shows both values with their source and no mean (`authored`).

#### Which direction is better

The comparison does not decide whether a higher score is better. Opik's
feedback definitions record name, type and range, and nothing about
direction. A score's name is not evidence either. The `Δ` is plain
arithmetic. Every score column whose cells carry a `Δ` has
`(direction unknown)` in its header (`_header`, `DIRECTION_UNKNOWN`). The
note under the header says a `+` means E2 scored higher, and that on a
lower-is-better metric a `+` is the regression (`_how_to_read`). Columns
with no `Δ` (one experiment, a label, two authors) are not marked.

#### Cases whose run errored

The joined row carries no error field, and an item's `status` is the
assertion verdict, passed or failed (`ComparedRow.errored`). The
server makes no trace or `error_info` call. It reads the error from the
row's shape:

- `-`: the experiment did not run the case.
- `unscored`: the experiment ran the case and recorded nothing for this
  score, or no experiment scored the case at all.
- `errored`: the experiment ran the case and recorded no score at all,
  while another experiment scored the same case. This is what a raised task
  or judge leaves behind.

The note under the table splits the page into fully scored, errored and
unscored cases whose counts add up to the rows, and tells the caller to open
an errored case's `worst_trace` for `error_info` (`_tally`). Failed
assertions have their own count. A page where every run scored gets no
such line.

`worst_trace` is the trace of the single run with the lowest sum of scores.
A run that recorded nothing sums to zero, so an errored run is usually the
one named. Ties go to the last experiment given. Within that experiment,
the first run that failed an assertion wins. A suite judged only by
assertions names the last experiment with a failed run, or nothing
(`_worst`). `reason` is the first failed assertion of that run, with its
name. A separate note counts the cases some experiment did not run, so a
`-` is read as "no run".

#### Figures per experiment

Under the count line, each experiment gets one line (runs, mean per
score, mean cost, median duration) from its own stats call with the page's
filters (`render_figures` in
`src/opik_mcp/read_list/entities/dataset/figures.py`). So "how many scored
under 0.5 in E1 and in E2" takes one filtered call and no rows. A categorical score is counted per label, with one extra
stats call per experiment and label. When those calls would go over
`CATEGORY_CALL_CAP`, none are made, and the note gives the filter that
counts one label (`_label_counts`, `figures_note`). A failed stats call
gives that experiment the line "figures unavailable" and the page is still
returned.

#### Filters, sort, search and fields on a comparison

- The run-level fields `feedback_scores`, `output` and `duration` are
  applied by the backend inside the join, which drops the other
  experiments' runs from matching rows. With two or more experiments, the
  server fetches each matched case again by id, in parallel, to restore the
  full row (`_with_every_run`). That is why such a filter caps `size` at
  `REFETCH_ROW_CAP`. A row whose refetch fails keeps what the filter
  returned, and the note names it (`_unrestored_note`). A run filter
  matches a case when any of its experiments matches.
- Case-level filters (`id`, `data.<key>`, `comments`) need no refetch.
- A sort on a score, cost, usage or duration orders by the value averaged
  across the compared runs. A sort on `output.*`, `input.*` or `metadata.*`
  uses the newest run's value. The note says so, because in neither case is
  it the baseline's own value (`_sort_caveat`).
- `search` matches the case data, not the runs' output. The note says to
  filter on `output` to search the output.
- On page 1 the note lists the runs' output keys (one extra call) and the
  case data keys, so the caller can filter and sort on them. A test suite's
  echoed `input` is hidden (`_keys_note`, `ECHOED_OUTPUT_KEY`).
- `fields=[…]` names the columns from `id`, `data.<key>`,
  `feedback_scores.<name>`, `worst_trace`, and `passed`/`reason` on a
  suite. Named cells are not cut, `worst_trace` is always kept, and an
  unknown name is refused with the valid ones (`compare_fields`,
  `_named_columns`).
- An empty page says which of four reasons applies: past the end, a run
  filter that matched nothing, a case filter or search that matched
  nothing, or experiments with no cases in common (`_empty`).

### Evaluation writes

`prompt_version.save`, `dataset.create` and `dataset_item.upsert` use
build hooks from `src/opik_mcp/writes/operations/evaluation.py`;
`experiment.create` and `experiment_item.create` use the generic pipeline
(`src/opik_mcp/writes/registry.py`). Validation, OAuth scopes and the error
envelope are in [writes](../writes/design-doc.md).

- `prompt_version.save` nests `template`, `commit`, `tags` and `metadata`
  under `version` and keeps `name` and `change_description` at the top
  (`build_prompt_version_save`). The prompt is created by name if it does
  not exist. The backend assigns the commit when none is given.
- `dataset.create` always sends `type`, a plain dataset by default. The
  caller writes `type="test_suite"` and the build sends the backend's
  `evaluation_suite` (`build_dataset_create`, `DATASET_TYPE_TO_WIRE` in
  `src/opik_mcp/writes/wire.py`).
- `dataset_item.upsert` takes one envelope,
  `{dataset_name | dataset_id, items: […]}`. Each item may be flat
  (`input`, `expected_output`, `metadata`) or already under `data`. The
  build folds the flat form into `data` and sets `source` to `sdk` when it
  is missing (`build_dataset_item_upsert`). Setting a key both at the top
  level and under `data` is a `data_field_conflict` (`DatasetItem` in
  `src/opik_mcp/writes/models.py`). Passing both or neither of the parent
  fields is `dataset_parent_conflict` or `dataset_parent_missing`.
- `experiment.create` takes exactly one of `dataset_name`/`dataset_id`,
  plus an optional name, metadata and `prompt_versions` (`ExperimentCreate`).
- `experiment_item.create` takes the `{experiment_items: […]}` array
  envelope. Each item links an experiment, a dataset item and a trace
  (`ExperimentItemCreate`).

The server does not run an evaluation. The skills do that, using the SDK
and these reads (see [skills](../skills/design-doc.md), `opik-evaluate` and
`opik-compare`).

## How it works

- `list('experiment')` / `read('experiment')`: `list_tool.py` or
  `read_tool.py` → `HANDLER` in
  `src/opik_mcp/read_list/entities/experiment.py` → `list_experiments` /
  `get_experiment` on the client. Columns come from `list_projection_fn`,
  and derived cells from `list_row_fn`.
- `list('dataset_item')` without `experiment_ids`: the shared collection
  path in `src/opik_mcp/read_list/list_tool.py` → `ITEM_HANDLER.list_fn`
  (`list_items`) → `list_dataset_items(dataset_id, …)`. Filters compile
  against `list_vocabulary="dataset_item_case"`.
- `list('dataset_item', experiment_ids=…)`: `run_when_kwargs` hands the
  whole call to `run_fn` (`run_compare`) through `_run_whole` in
  `src/opik_mcp/read_list/list_tool.py`, which returns
  `run_timeout_hint` on a timeout. `run_compare` resolves the experiments
  with `get_experiment`. It then sends, in one `asyncio.gather`,
  `list_compared_dataset_items`, `list_feedback_definitions`, one
  `get_compared_stats` per experiment and, on page 1,
  `list_compared_output_columns`. Only the rows call can fail the answer.
  Label counts, then row refetches, follow as parallel batches.
- `src/opik_mcp/read_list/entities/dataset/` holds `dataset` and
  `dataset_item`, since an item exists only under a dataset: `compare.py`
  validates and fetches, `layout.py` draws the table, `figures.py` the
  per-experiment lines, `items.py` the plain listing and one-case read.
- `src/opik_mcp/read_list/sample.py` holds `THIN_SAMPLE`. Both the
  experiment ranking caveat and the comparison guard need it, and one
  entity may not import another
  ([ADR 0004](../decisions/0004-entity-logic-in-its-namespace.md)).
- The filter and sort tables for `experiment`, `dataset_item` and
  `dataset_item_case` live in the root `oql.py` and `sorting.py`, owned by
  [tool-surface](../tool-surface/design-doc.md). This feature reads them.

## Decisions

- No direction for a score: Opik's feedback definition has no direction
  field, and a name like `hallucination` can be scored either way. The `Δ`
  stays arithmetic, and the header marks it `direction unknown` where it is
  read (#195).
- Score kind comes from feedback definitions, fetched once per comparison
  call. A row's `category_name` also marks a score as categorical when no
  definition exists (#195).
- Errored is worked out from the row, with no trace query. The compare
  endpoint returns no error, so a run with no scores beside a scored run is
  shown as `errored` and counted apart from low scores (#195).
- Categorical labels are counted per experiment only up to
  `CATEGORY_CALL_CAP` extra stats calls. Beyond that, the note gives the
  filter that counts one label (#192).
- Every cut is stated with its count and the call that gets the rest:
  omitted keys and scores, cut cells, unrestored rows
  ([ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md)).
- The comparison is a mode of `list('dataset_item')`, so the tool set does
  not grow ([ADR 0003](../decisions/0003-five-tool-surface.md), #190).
- One shared cell per score: a column per experiment per score does not fit
  the page budget (`layout.py` module docstring, #190).
- A run-level filter triggers a refetch of each row, capped by
  `REFETCH_ROW_CAP`, so a filtered comparison still shows every run. The
  backend has no list operator for `id` on this endpoint (#190).
- Filters and sorts the backend accepts and then ignores are refused before
  the call. Otherwise the page would look filtered or sorted when it is not
  (#190, #196).
- `list('experiment')` shows the whole record the backend already sends
  (status, versions, cost, duration, assertion runs), with columns chosen
  from the page, so the caller does not need a read per row (#192).
- `run_experiment` was removed on 2026-09-03 (#181). It submitted a run
  and returned ids at once, because experiments are long-running jobs.
  Running an evaluation is now the skills' job.
- Unverified: that host timeouts were the reason for fire-and-return, and
  why the tool was removed; #181 does not say.
- Not built: no evaluation operation deletes anything
  (`src/opik_mcp/writes/registry.py`); [writes](../writes/design-doc.md)
  gives the reasons.

## Proven by

- `tests/test_read_list/test_dataset_compare.py`: table shape, legend,
  refusals, guards, suite columns, worst trace, run-filter refetch and its
  cap, sort and search notes, `errored`/`unscored` tally, cell escaping.
- `tests/test_read_list/test_dataset_compare_figures.py`: signed `Δ` and the
  direction note, labels never averaged, two authors shown, figures per
  experiment following the filter, label-count cap, figures failure keeps
  the page.
- `tests/test_read_list/test_experiments.py`: core and conditional columns,
  derived cells, empty-page notes, thin-sample ranking caveat.
- `tests/test_read_list/test_dataset_items.py`: data-key columns and cuts,
  the `dataset_item_case` filters, sort refused, fields of the other call
  pointed at `experiment_ids`, `read('dataset_item', id)` uncut, aliases.
- `tests/e2e/test_compare_experiments_surface.py`: request shape against
  the stub backend over stdio, constant cost on a large suite, refetch
  restores runs, `errored` end to end, `fields` on a comparison, and
  `test_reading_an_experiment_names_the_call_that_compares_it`.
- `tests/e2e/test_find_a_case_surface.py`: the items route takes the
  filters array, case keys reach the backend in its map form, one case is
  addressed without its dataset.
- `tests/test_writes/test_dispatch.py`:
  `test_dataset_create_sends_the_backends_spelling_of_a_test_suite`,
  `test_dataset_create_always_sends_a_type`,
  `test_prompt_version_save_wraps_in_version_envelope`,
  `test_an_envelope_upsert_counts_its_cases_not_its_envelope`.
- `tests/test_writes/test_models.py`:
  `test_dataset_item_upsert_rejects_data_field_conflict`,
  `test_dataset_item_upsert_both_parent_conflict`,
  `test_experiment_create_missing_dataset_parent`,
  `test_experiment_item_create_bare_object_returns_envelope_example`.

## Log

- 2026-09-23: experiment rows and dataset reads carry a working UI link, so the user can open them (#201).
- 2026-09-22: the experiment footer names every filterable field, so the agent can narrow without guessing (#199).
- 2026-09-21: `fields=[…]` on a comparison, with `worst_trace` always kept so a row still opens its trace (#197).
- 2026-09-21: find a case by keys, payload, id, tag or source trace, and read one case uncut, without knowing its id (#196).
- 2026-09-21: `direction unknown` marker, `errored` cells and cell escaping, so the table does not mislead (#195).
- 2026-09-21: full experiment record in `list('experiment')`, with figures and label counts, so no read per row is needed (#192).
- 2026-09-17: experiments compared case by case in `list('dataset_item')`, so a regression is found in one call (#190).
- 2026-09-17: suite writes became `dataset.create` (with a `type`) and `dataset_item.upsert`, since a suite is a dataset (#190).
- 2026-09-11: experiment and dataset handlers moved under `entities/`, one namespace per entity (#187).
- 2026-09-10: evaluation write hooks moved into `writes/operations/`, out of the generic dispatcher (#186).
- 2026-09-03: `run_experiment` tool removed; running an evaluation became the skills' job (#181).
