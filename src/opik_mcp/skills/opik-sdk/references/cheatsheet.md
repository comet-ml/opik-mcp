---
name: opik-sdk-cheatsheet
description: Always-loaded primer covering the most-used data methods on opik.Opik and the OQL filter language. Full SDK reference remains on demand via read_skill("opik-sdk").
last_updated: "2026-06-17"
source_commit: "9900fa181de0ed970958259187a3ce9ddac2045b"
---

# Opik SDK Cheatsheet

This is the always-loaded **high-level primer** — orientation only. It exists so you have a working mental model of the SDK's data surface (`opik.Opik` methods, dataset/test-suite item operations, manual logging, agent config, feedback scores) and the **OQL** filter language even before you start writing.

**It is not a substitute for the full reference.** Before writing Python code that uses the Opik SDK, make sure `read_skill("opik-sdk")` has been called in this conversation and is still visible in your context. If you've already loaded it earlier in the same conversation and it hasn't been evicted by context compaction, you can proceed without reloading. Otherwise — or if you're about to use a method, kwarg, or operator the cheatsheet doesn't show — load it. The cheatsheet covers shape and naming; the full reference is the authoritative source for signatures, return types, Pydantic schemas, and parameter semantics.

---

## Client initialisation

```python
import opik

client = opik.Opik()  # reads OPIK_API_KEY, OPIK_WORKSPACE, OPIK_URL_OVERRIDE from env
```

The subprocess environment is pre-configured. Do not pass `api_key=`, `workspace=`, or `url=` arguments to the constructor, and do not call `opik.configure()`.

For low-level REST endpoints not exposed on the high-level client, use `client.rest_client.<resource>.<method>(...)`. Construct the high-level client first; never instantiate `OpikApi()` / `AsyncOpikApi()` directly.

---

## Retrieval — search and fetch

### Traces

```python
client.search_traces(
    project_name: str | None = None,
    filter_string: str | None = None,
    max_results: int = 1000,
    truncate: bool = True,
    exclude: list[str] | None = None,
) -> list[TracePublic]

client.get_trace_content(id: str) -> TracePublic
```

### Spans

```python
client.search_spans(
    project_name: str | None = None,
    trace_id: str | None = None,
    filter_string: str | None = None,
    max_results: int = 1000,
    truncate: bool = True,
    exclude: list[str] | None = None,
) -> list[SpanPublic]

client.get_span_content(id: str) -> SpanPublic
```

`search_spans` filters by `trace_id` directly via that kwarg, but every other constraint (time, name, type, error presence) goes through `filter_string`.

### Threads

```python
client.search_threads(
    project_name: str | None = None,
    filter_string: str | None = None,
    max_results: int = 1000,
    truncate: bool = True,
) -> list[TraceThread]
```

### Datasets

```python
client.get_dataset(name: str, project_name: str | None = None) -> Dataset
client.get_datasets(max_results: int = 100, sync_items: bool = True, project_name: str | None = None) -> list[Dataset]
client.get_dataset_experiments(dataset_name: str, max_results: int = 100, project_name: str | None = None) -> list[Experiment]
```

The `Dataset` object has its own specialised API (item insert/update/delete, `get_items`, version views, pandas/JSONL IO) — load the full reference if you need it.

### Experiments

```python
client.get_experiment_by_id(id: str) -> Experiment
client.get_experiments_by_name(name: str, project_name: str | None = None) -> list[Experiment]
```

The `Experiment` object has its own specialised API (item insertion, dataset version association, evaluation results) — load the full reference if you need it.

### Test suites

```python
client.get_test_suite(name: str, project_name: str | None = None) -> TestSuite
client.get_test_suites(max_results: int = 100, project_name: str | None = None) -> list[TestSuite]
client.get_test_suite_experiments(name: str, max_results: int = 100, project_name: str | None = None) -> list[Experiment]
```

The `TestSuite` object has its own specialised API (item operations, version views, run helpers) — load the full reference if you need it.

### Projects

```python
client.get_project(id: str) -> ProjectPublic
client.get_project_url(project_name: str | None = None) -> str
```

---

## Creation

### Datasets

```python
client.create_dataset(name: str, description: str | None = None, project_name: str | None = None) -> Dataset
client.get_or_create_dataset(name: str, description: str | None = None, project_name: str | None = None) -> Dataset
```

`get_or_create_dataset` is idempotent and the right default for "ensure this dataset exists." Use `create_dataset` only when you specifically want creation to fail on a name collision.

### Test suites

```python
client.create_test_suite(name: str, description: str | None = None, ...) -> TestSuite
client.get_or_create_test_suite(name: str, description: str | None = None, ...) -> TestSuite
```

### Dashboards

```python
client.create_dashboard(
    name: str,
    type: str | None = None,           # "multi_project" (default) | "experiments"
    description: str | None = None,
    project_name: str | None = None,   # project-scoped dashboard; created if absent
    project_id: str | None = None,     # takes precedence over project_name
    sections: list | None = None,      # DashboardSection objects or dicts; defaults to one empty section
) -> Dashboard

client.get_dashboard(dashboard_id: str) -> Dashboard
client.get_dashboards(name: str | None = None, project_id: str | None = None, max_results: int = 100) -> list[Dashboard]
client.delete_dashboard(dashboard_id: str) -> None
```

The returned `Dashboard` object has mutation methods that patch the backend atomically:

```python
dashboard.add_widget(widget, *, section_id=None, size=None) -> str       # returns new widget id; auto-places on grid
dashboard.remove_widget(widget_id: str) -> None
dashboard.update_widget(widget_id, *, title=None, subtitle=None, config=None) -> None  # config is merged, not replaced
dashboard.add_section(title: str) -> str                                  # returns new section id
dashboard.replace_sections(sections: list) -> None                        # reorder / move widgets between sections
dashboard.rename(name: str) -> None
dashboard.set_description(description: str) -> None
dashboard.reload() -> None                                                # re-sync before mutating if concurrent edits possible
dashboard.delete() -> None
```

Build widgets with typed config models from `opik.api_objects.dashboard`:

```python
from opik.api_objects.dashboard import (
    DashboardWidget, DashboardSection,
    ProjectMetricsConfig, ProjectMetricType,
    ProjectStatsCardConfig, StatsCardMetric,
    TextMarkdownConfig, ExperimentsFeedbackScoresConfig, ExperimentLeaderboardConfig,
    DashboardType, WidgetType,
)

widget = DashboardWidget(
    type=WidgetType.PROJECT_METRICS,
    title="Trace count over time",
    config=ProjectMetricsConfig(metric_type=ProjectMetricType.TRACE_COUNT),
)
dashboard.add_widget(widget)
```

`project_metrics` and `project_stats_card` widgets require a project-scoped dashboard (`project_name` or `project_id` passed to `create_dashboard`); the SDK raises `DashboardValidationError` otherwise.

`create_dashboard` always creates one section named **"Overview"** by default — `add_section` is only needed for additional sections. To add widgets from the start, pass them via `sections` to `create_dashboard` rather than calling `add_widget` in a second pass.

See `opik-sdk/common-mistakes` for the metric namespace gotcha (`StatsCardMetric` vs `ProjectMetricType` — the enums do not map 1:1; e.g. `COST` exists only on `ProjectMetricType`, the stat-card equivalent is `TOTAL_ESTIMATED_COST_SUM`).

---

## Reading results

`search_*` and `get_*_content` methods return **Pydantic models**, not dicts. Access fields with **dot notation**; call `.model_dump()` for a dict. There is no `.get(...)` on these objects — use `getattr(obj, "field", default)` if you need a fallback. Anything not listed below does not exist on the object.

### `TracePublic` and `SpanPublic` — shared fields

Both expose the same shape for the common accesses, so the same code reads either:

| Field | Type | Notes |
|---|---|---|
| `id`, `name` | `str \| None` | |
| `start_time` | `datetime` | required |
| `end_time` | `datetime \| None` | |
| `duration`, `ttft` | `float \| None` | **milliseconds**, sub-ms precision |
| `input`, `output`, `metadata` | JSON-shaped | typed wrapper; access as Python data |
| `tags` | `list[str] \| None` | |
| `usage` | `dict[str, int] \| None` | **dict access**: `obj.usage["total_tokens"]`, `obj.usage["prompt_tokens"]`, `obj.usage["completion_tokens"]` |
| `total_estimated_cost` | `float \| None` | NOT `.cost` |
| `error_info` | `ErrorInfoPublic \| None` | nested object — see below |
| `feedback_scores` | `list[FeedbackScorePublic] \| None` | iterate to find by name |
| `comments` | `list[CommentPublic] \| None` | |
| `project_id` | `str \| None` | plus `created_at`, `last_updated_at`, `created_by`, `last_updated_by` |

**Trace-only**: `span_count` (int), `llm_span_count` (int), `has_tool_spans` (bool), `providers` (`list[str]`), `span_feedback_scores` (list, aggregated from spans), `guardrails_validations`, `thread_id`, `experiment`, `visibility_mode`.

**Span-only**: `type` (`"general" | "tool" | "llm" | "guardrail"`), `trace_id`, `parent_span_id`, `model`, `provider`, `project_name`.

### `TraceThread` (returned by `search_threads`)

| Field | Type | Notes |
|---|---|---|
| `id` | `str \| None` | |
| `start_time`, `end_time` | `datetime \| None` | |
| `duration` | `float \| None` | ms |
| `first_message`, `last_message` | JSON-shaped | |
| `number_of_messages` | `int \| None` | use this, not `.message_count` |
| `feedback_scores` | `list[FeedbackScore] \| None` | |
| `status` | enum | |
| `total_estimated_cost` | `float \| None` | |
| `usage` | `dict[str, int] \| None` | dict access |
| `comments`, `tags` | lists | |

### Nested types

- **`ErrorInfoPublic`** (the `error_info` field): `exception_type` (str), `message` (str | None), `traceback` (str).
- **`FeedbackScorePublic`** (each entry in `feedback_scores` lists): `name` (str), `value` (float), `category_name` (str | None), `reason` (str | None), `source` (enum), `value_by_author` (dict | None).

### Common access patterns

```python
traces = client.search_traces(project_name="my-project", filter_string="error_info is_not_empty")

# Counts
total = len(traces)
total_with_errors = sum(1 for t in traces if t.error_info is not None)

# Cost / tokens
total_cost = sum((t.total_estimated_cost or 0) for t in traces)            # .total_estimated_cost, not .cost
prompt_tokens = sum((t.usage or {}).get("prompt_tokens", 0) for t in traces)  # usage IS a dict — .get works
total_tokens = sum((t.usage or {}).get("total_tokens", 0) for t in traces)

# Error info — structured object, not a string
for trace in traces:
    if trace.error_info:
        print(trace.error_info.exception_type, "-", trace.error_info.message)

# Feedback scores — list of FeedbackScorePublic, not a dict
def score(trace, name):
    return next((s.value for s in (trace.feedback_scores or []) if s.name == name), None)

accuracy = score(traces[0], "accuracy")

# Span counts on a trace — pre-computed fields, no fetch needed
print(traces[0].span_count, "spans;", traces[0].llm_span_count, "are LLM spans")

# Fetching all spans of one trace
spans = client.search_spans(project_name="my-project", trace_id=traces[0].id)

# Iterating spans, filtering LLM spans manually
llm_spans = [s for s in spans if s.type == "llm"]
for s in llm_spans:
    print(s.model, s.provider, s.usage and s.usage.get("total_tokens"))
```

---

## OQL — `filter_string` syntax

Used by every `search_*` method, `get_*_history`, `dataset.get_items`, `test_suite.get_items`, etc.

### Grammar

```
<column> <operator> <value> [AND <column> <operator> <value>]*
```

- Multiple conditions are combined with **`AND`** (uppercase).
- **`OR` is not supported.** Run multiple queries and merge in Python if you need disjunction.
- Values are typed: strings in quotes (`"x"` or `'x'`), numbers bare, datetimes as quoted ISO 8601 with `Z` suffix.

### Operators by column type

| Column type | Operators |
|---|---|
| String (`id`, `name`, `input`, `output`, `model`, `provider`, etc.) | `=`, `!=`, `contains`, `not_contains`, `starts_with`, `ends_with`, `>`, `<` |
| Number (`duration`, `total_estimated_cost`, `usage.*`, `llm_span_count`, `number_of_messages`) | `=`, `!=`, `>`, `>=`, `<`, `<=` |
| Date/time (`start_time`, `end_time`, `created_at`, `last_updated_at`) | `=`, `!=`, `>`, `>=`, `<`, `<=` |
| List (`tags`, `annotation_queue_ids`) | `=`, `!=`, `contains`, `not_contains`, `is_empty`, `is_not_empty` |
| Dictionary (`metadata`, `input_json`, `output_json`) | `=`, `!=`, `contains`, `not_contains`, `starts_with`, `ends_with`, `>`, `>=`, `<`, `<=` |
| Feedback scores (`feedback_scores.<name>`, `span_feedback_scores.<name>`) | `=`, `!=`, `>`, `>=`, `<`, `<=`, `is_empty`, `is_not_empty` |
| `error_info` | `is_empty`, `is_not_empty` *only* |
| Enum (`type` on spans, `status` on threads) | `=`, `!=` |

### Filterable columns by entity

**Traces (`search_traces`)**: `id`, `name`, `input`, `output`, `input_json`, `output_json`, `metadata`, `start_time`, `end_time`, `created_at`, `last_updated_at`, `total_estimated_cost`, `llm_span_count`, `tags`, `usage.total_tokens`, `usage.prompt_tokens`, `usage.completion_tokens`, `feedback_scores`, `span_feedback_scores`, `duration`, `thread_id`, `guardrails`, `error_info`, `annotation_queue_ids`, `experiment_id`.

**Spans (`search_spans`)**: `id`, `name`, `input`, `output`, `input_json`, `output_json`, `metadata`, `model`, `provider`, `total_estimated_cost`, `tags`, `usage.total_tokens`, `usage.prompt_tokens`, `usage.completion_tokens`, `feedback_scores`, `duration`, `error_info`, `type`, `trace_id`, `start_time`, `end_time`.

**Threads (`search_threads`)**: `id`, `first_message`, `last_message`, `number_of_messages`, `duration`, `created_at`, `last_updated_at`, `start_time`, `end_time`, `feedback_scores`, `status`, `tags`, `annotation_queue_ids`.

### Nested key access

Dictionary fields support a single dot-suffix key:

- `metadata.environment = "prod"` — accesses `metadata["environment"]`
- `feedback_scores.accuracy >= 0.8` — score named "accuracy"
- `usage.total_tokens > 10000` — usage fields are flat (only `total_tokens`, `prompt_tokens`, `completion_tokens` are exposed)

### Examples

```python
# All traces in a project
client.search_traces(project_name="my-project")

# Time-bound
client.search_traces(
    project_name="my-project",
    filter_string='start_time >= "2026-04-29T00:00:00Z"',
)

# Errored traces only
client.search_traces(
    project_name="my-project",
    filter_string="error_info is_not_empty",
)

# Combined conditions (AND only)
client.search_traces(
    project_name="my-project",
    filter_string='name = "agent_turn" AND start_time >= "2026-04-29T00:00:00Z" AND error_info is_not_empty',
)

# By tag
client.search_traces(
    project_name="my-project",
    filter_string='tags contains "production"',
)

# By feedback score
client.search_traces(
    project_name="my-project",
    filter_string="feedback_scores.accuracy >= 0.8",
)

# By metadata key
client.search_traces(
    project_name="my-project",
    filter_string='metadata.environment = "staging"',
)

# Spans of a specific type with high token usage
client.search_spans(
    project_name="my-project",
    filter_string='type = "llm" AND usage.total_tokens > 10000',
)

# Spans within a known trace, errored
client.search_spans(
    project_name="my-project",
    trace_id="abc-123",
    filter_string="error_info is_not_empty",
)
```

---

## Common mistakes — `read_skill("opik-sdk/common-mistakes")`

Before reaching for `client.rest_client.*` or `ConfigManager`, check the **common-mistakes** reference. It lists the REST sub-resource names that don't exist (`comments`, `agent_configurations`, `dataset_items`, `experiment_items`, `spans.get_spans_of_trace`), the methods whose names look right but aren't (`get_active_blueprint`, `find_agent_configurations`), the field-name surprises (`.dataset_item` vs `.dataset_item_data`), the `FieldValueSpec` wrapping rule for `ConfigManager.update_blueprint`, and the methods that return scalar IDs instead of rich objects (`create_config`, `create_mask`). If the `opik_sdk` tool surfaces a `[hint]` line in a failure, load this file before rewriting.

## This is NOT a full reference — call `read_skill("opik-sdk")` tool if it isn't already in context

Before writing Python code that uses the Opik SDK, make sure `read_skill("opik-sdk")` has been called in this conversation and is still visible in your context. If you've already loaded it earlier in the same conversation and it hasn't been evicted by compaction, you can proceed without reloading. Otherwise — or if you're about to use any method, kwarg, or filter operator that the cheatsheet doesn't show — call it.

Treat the cheatsheet as orientation; treat the full reference as ground truth. Working from this cheatsheet alone (when the full reference isn't loaded) is the single largest preventable cause of script failures (`AttributeError`, wrong kwargs, malformed filters).
