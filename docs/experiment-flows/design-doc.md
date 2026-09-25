# experiment-flows

## Purpose

This feature lists and reads experiments and datasets, finds a case in a
dataset, compares experiments case by case, and sends the writes an
evaluation needs. Open it to learn what a comparison page promises, how a
case is found, or what the evaluation writes send to the backend.

## What it does now

### Experiments

- `list('experiment')` returns one row per run, newest first, and takes
  `filters` and `sort`. `since`/`until` are refused because the backend has
  no time window for experiments. The refusal says to page instead.
- Every row carries `trace_count` beside the scores. Other columns appear
  only when some row on the page has a value. The note names the omitted
  ones and every filterable field (`project_experiments`). A row missing its
  project, dataset or id gets no UI link (`experiment_links`).
- On a page sorted by a score, if either top row covers under `THIN_SAMPLE`
  cases, a note says the order is not settled (`_ranking_caveat`).
- `read('experiment', id)` adds `comparePerCase`, the comparison call to
  make next, when the run has a dataset.

### Datasets and cases

- `read('dataset')` returns the record without items. A dataset created at
  workspace level has no UI page, and the answer says so (`dataset_links`).
- `list('dataset_item', dataset_id=…)` lists the dataset's cases. Columns
  are the `data` keys found on the page, capped, with omitted keys named.
  Cells share a page budget and every cut is stated.
  `read('dataset_item', id)` returns one case uncut, without the dataset.
- Filters use the `dataset_item_case` vocabulary: `data.<key>`,
  `full_data`, `id`, `tags`, `source`, `trace_id`, `span_id`, timestamps,
  authors. A filter on `trace_id` finds the case built from that trace.
- Run fields (`duration`, `output`, `comments`, `feedback_scores`) are
  refused with the `experiment_ids` call that accepts them. Cost and usage
  are refused on both calls. Every `sort` is refused, since the endpoint has
  no sorting parameter.

### Comparing: `list('dataset_item', experiment_ids=[A, B])`

- One row per case, with each experiment's runs joined on by the backend, so
  a page costs the same at any dataset size. The first id is the baseline.
  The header gives the legend for `E1`, `E2`, ….
- Refused before any fetch: bad or repeated ids, more than
  `MAX_EXPERIMENTS`, `since`/`until`, experiments of different datasets or
  with none, a `dataset_id` other than theirs, a sort field the endpoint
  does not order by, and a run filter with `size` above `REFETCH_ROW_CAP`.
- `_guards` warns about different dataset versions, a running run, and
  different case counts. Columns: `id`, case keys, the most common scores,
  `worst_trace`, and `passed`/`reason` for a test suite. Those two read the
  assertion results that also set an item's `status`: passed/total runs per
  experiment, and the first failed assertion of the worst run.
- Experiments share one cell per score, separated by ` / `. A numeric value
  is the mean of that experiment's runs. With exactly two experiments it
  adds `Δ`, E2 minus E1, and the header says `direction unknown`. Three or
  more get no `Δ`. A categorical score shows labels. A score written by two
  authors shows both values with their source.
- `-` means the experiment did not run the case. `unscored` means it ran and
  recorded nothing for this score. `errored` is inferred from the row: the
  experiment's runs carry no score under any name, including scores not
  shown as columns, while another experiment scored the case
  (`_scoreless`). It does not prove the task raised. The note counts fully
  scored, errored and unscored cases (`_tally`).
- `worst_trace` is the run with the lowest sum of its scores, treating
  higher as better whatever the direction. In an assertion-only suite it is
  the last experiment's failed run (`_worst`).
- One line per experiment gives runs, mean scores, cost and median duration
  under the page's filters (`render_figures`). Labels are counted up to
  `CATEGORY_CALL_CAP` extra calls. A failed stats call keeps the page.
- A filter on `feedback_scores`, `output` or `duration` matches a case when
  any experiment matches. Each matched row is refetched to restore the other
  runs, and a row whose refetch fails is named in the note.
- A sort on a score, cost, usage or duration orders by the mean across runs.
  A sort on `output.*`, `input.*` or `metadata.*` uses the newest run. The
  note says which (`_sort_caveat`).
- `search` matches case data only. `fields=[…]` always keeps `worst_trace`,
  refuses unknown names, and drops the note listing output and case keys.

### Evaluation writes

- `prompt_version.save` nests `template`, `commit`, `tags` and `metadata`
  under `version`. `dataset.create` always sends `type`.
- `dataset_item.upsert` takes `{dataset_name | dataset_id, items: […]}`.
  Flat `input`, `expected_output` and `metadata` fold into `data`, and
  `source` defaults to `sdk`. A key set both flat and under `data` is
  `data_field_conflict`.
- The rest use the generic [writes](../writes/design-doc.md) pipeline. The
  [skills](../skills/design-doc.md) run evaluations; this server does not.

## How it works

```
list/read → read_list/registry.py → entities/experiment.py
                                   → entities/dataset/__init__.py
  dataset_item, no experiment_ids → items.py (list_items, fetch_item)
  dataset_item + experiment_ids   → compare.py run_compare
      → get_experiment per id, then one gather: rows, feedback
        definitions, stats per experiment, output columns (page 1)
      → figures.py (per-experiment lines), layout.py render
write → writes/registry.py → writes/operations/evaluation.py build hooks
```

- To add an experiment column, start at `_CONDITIONAL` and `derive_columns`
  in `src/opik_mcp/read_list/entities/experiment.py`. Dotted names such as
  `duration.p90` resolve through `columns.resolve`.
- For a comparison refusal or fetch, start in
  `src/opik_mcp/read_list/entities/dataset/compare.py`; for a cell or note,
  `layout.py`.
- The item vocabularies (`COMPARED` for the comparison, `CASES` for the
  dataset's own items) are in `src/opik_mcp/read_list/entities/dataset/vocabulary.py`.
  How filters and sort are checked against them, aliases, the `run_fn` handoff
  and link building are in [tool-surface](../tool-surface/design-doc.md).

## Decisions

- The comparison is a mode of `list('dataset_item')`, so the tool set stays
  at five ([ADR 0003](../decisions/0003-five-tool-surface.md), #190).
- `Δ` is plain arithmetic. Opik's feedback definitions have no direction
  field, and a name like `hallucination` can be scored either way (#195).
- `errored` is inferred from the row, with no trace query, because the
  compare endpoint returns no error (#195).
- A run filter refetches each row, capped by `REFETCH_ROW_CAP`, because the
  endpoint has no list operator for `id` (#190).
- Filters and sorts the backend accepts and then ignores are refused, or the
  page would look filtered or sorted when it is not (#190, #196).

### Traps

- An experiment run with no scorers, compared with one that has scorers,
  shows every case as `errored`. The rule cannot tell a raised task from a
  missing judge.
- The caller writes `type="test_suite"`. The backend stores
  `evaluation_suite` (`TEST_SUITE_METHOD`, `DATASET_TYPE_TO_WIRE`).

## Proven by

- Comparison table, refusals, guards, refetch, sort notes and the tally:
  `tests/read_list/test_dataset_compare.py`.
- `Δ` and direction note, labels, two authors, figures and the label-count
  cap: `tests/read_list/test_dataset_compare_figures.py`.
- The comparison over stdio, including constant page cost
  (`test_a_comparison_costs_the_same_on_twenty_and_on_a_hundred_thousand_cases`):
  `tests/e2e/test_compare_experiments_surface.py`.
- Experiment columns, notes and ranking caveat:
  `tests/read_list/test_experiments.py`.
- Case listing, filters, refused sorts, one-case read, aliases:
  `tests/read_list/test_dataset_items.py`, `tests/e2e/test_find_a_case_surface.py`.
- Links: `tests/read_list/test_link_shape.py`, `tests/e2e/test_entity_links_surface.py`.
- Evaluation write bodies and their validation errors:
  `tests/writes/test_dispatch.py`, `tests/writes/test_models.py`.

## Log

- 2026-09-23: experiment rows and dataset reads carry a working UI link (#201).
- 2026-09-21: `direction unknown` marker and `errored` cells, so the table does not mislead (#195).
- 2026-09-21: full experiment record in `list('experiment')`, so no read per row is needed (#192).
- 2026-09-17: experiments compared case by case in `list('dataset_item')` (#190).
- 2026-09-03: `run_experiment` removed; the skills run evaluations (#181).
