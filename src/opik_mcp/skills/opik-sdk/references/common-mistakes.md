---
name: opik-sdk-common-mistakes
description: Catalogue of recurring Opik SDK code-generation mistakes — hallucinated REST paths, wrong field names, wrong argument shapes, sandbox-unavailable imports. Each entry shows the broken pattern and the correct replacement. Load after a script fails with one of these errors.
last_updated: "2026-06-17"
source_commit: "9900fa181de0ed970958259187a3ce9ddac2045b"
---

# Opik SDK — Common Mistakes

This reference captures the *wrong* patterns the agent tends to write and the *right* replacement for each. The patterns are derived from production opik_sdk failures; the corrections are verified against the installed `opik` package.

Use this file as a checklist before writing low-level REST calls (`client.rest_client.*`) or working with `ConfigManager`. The cheatsheet is the orientation; this file is the catch list.

---

## Hallucinated `rest_client` sub-resources

The high-level client is `opik.Opik()`. Below it sits `client.rest_client` (sync `OpikApi`). When you reach for a sub-resource on `rest_client`, the name has to match the real REST surface — guessing produces `AttributeError` at runtime.

### Comments

```python
# WRONG: there is no comments resource on rest_client.
client.rest_client.comments.add_comment(trace_id=..., text=...)
```

```python
# RIGHT: comments are a field on the trace itself, set via update_trace.
client.update_trace(id=trace_id, comments=[{"text": "..."}])
```

### Agent configurations

The REST resource is `agent_configs`, not `agent_configurations`.

```python
# WRONG
client.rest_client.agent_configurations.find_agent_configurations(...)
client.rest_client.agent_configs.find_agent_configurations(...)
client.rest_client.agent_configs.get_active_blueprint(project_id)
```

```python
# RIGHT
project = client.rest_client.projects.retrieve_project(name=project_name)
# Latest version:
bp = client.rest_client.agent_configs.get_latest_blueprint(project.id)
# Specific named version:
bp = client.rest_client.agent_configs.get_blueprint_by_name(project_id=project.id, name="v2")
```

### Dataset items

```python
# WRONG: no rest_client.dataset_items resource for listing items.
items = client.rest_client.dataset_items.get_dataset_items(dataset_id=...)
```

```python
# RIGHT: use the high-level Dataset object.
dataset = client.get_dataset(name="my-dataset")
items = dataset.get_items()
```

### Experiment items

```python
# WRONG
items = client.rest_client.experiments.get_experiment_items(experiment_id=...)
```

```python
# RIGHT
experiment = client.get_experiment_by_id(experiment_id)
items = experiment.get_items()
```

### Spans of a trace

```python
# WRONG
spans = client.rest_client.spans.get_spans_of_trace(trace_id=...)
```

```python
# RIGHT
spans = client.search_spans(trace_id=trace_id, project_name=project_name)
```

---

## Wrong field names

### `ExperimentItemContent.dataset_item`

```python
# WRONG: AttributeError — the field is named differently.
for item in experiment.get_items():
    payload = item.dataset_item
```

```python
# RIGHT
for item in experiment.get_items():
    payload = item.dataset_item_data
```

---

## Wrong argument types

### `ConfigManager.update_blueprint(fields_with_values=...)`

`fields_with_values` is `Dict[str, FieldValueSpec]`, not `Dict[str, Any]`. Passing raw values raises `TypeError: Unsupported type: <name>`.

```python
# WRONG
config_manager.update_blueprint(
    fields_with_values={"prompt": "You are a helpful assistant"},
)
```

```python
# RIGHT
from opik.api_objects.agent_config.types import FieldValueSpec

config_manager.update_blueprint(
    fields_with_values={
        "prompt": FieldValueSpec(python_type=str, value="You are a helpful assistant"),
    },
)
```

The same wrapping applies to `ConfigManager.create_blueprint` and `ConfigManager.create_mask`.

---

## Return types that surprise

A few methods return scalar IDs rather than the rich object you'd expect from the name. Read the SDK reference's signature line before chaining attribute access on the result.

| Method | Returns | Not |
|---|---|---|
| `client.create_config(config, project_name=..., description=...)` | `str` (the new config's ID) | a `Config` object |
| `ConfigManager.create_mask(...)` | `str` (the mask blueprint's ID) | a `Blueprint` object |
| `ConfigManager.update_blueprint(...)` | `Blueprint` | a list of fields |

If you need the full object after `create_config`, follow up with `client.get_or_create_config(...)` or fetch the blueprint by ID via `client.rest_client.agent_configs.get_blueprint_by_id(blueprint_id=...)`.

---

## Unavailable modules in the sandbox

Only `opik`, `pandas`, `numpy`, and the Python standard library are installed. No external network access — `requests`, `urllib`, `httpx`, `aiohttp` cannot reach external services even when they import.

| You might reach for | Why it fails | What to do instead |
|---|---|---|
| `matplotlib`, `seaborn`, `plotly` | Not installed | Return a pandas DataFrame; the frontend renders it. Print summary stats. |
| `requests`, `httpx`, `aiohttp`, `urllib3` | Not installed (or no network) | The opik client already has the credentials and network path to Opik. For anything else, ask the user. |
| `scipy`, `sklearn`, `polars`, `pyarrow` | Not installed | Use pandas/numpy primitives. |
| `openai`, `anthropic`, `langchain`, `litellm` | Not installed | The sandbox runs analysis, not inference. Use Opik to inspect existing traces/spans. |

---

## Dashboard metric namespaces

Two separate metric ID namespaces exist for dashboard widgets. Both are typed enums in `opik.api_objects.dashboard`, but it's easy to pick the wrong one because `"trace_count"` and `"TRACE_COUNT"` look like the same concept.

| Widget type | Config field / model | Namespace | Example value |
|---|---|---|---|
| `project_stats_card` | `ProjectStatsCardConfig.metric` | `StatsCardMetric` — **lowercase-dotted** strings | `"duration.p50"`, `"trace_count"` |
| `project_metrics` | `ProjectMetricsConfig.metric_type` | `ProjectMetricType` — **ALL-CAPS** enum values | `"DURATION"`, `"TRACE_COUNT"` |

```python
# WRONG — namespaces crossed
from opik.api_objects.dashboard import ProjectStatsCardConfig, ProjectMetricsConfig

ProjectStatsCardConfig(metric="DURATION")           # "DURATION" is ProjectMetricType, not StatsCardMetric
ProjectMetricsConfig(metric_type="duration.p50")    # "duration.p50" is StatsCardMetric, not ProjectMetricType
```

```python
# RIGHT
from opik.api_objects.dashboard import (
    ProjectStatsCardConfig, StatsCardMetric,
    ProjectMetricsConfig, ProjectMetricType,
)

ProjectStatsCardConfig(metric=StatsCardMetric.DURATION_P50)         # "duration.p50"
ProjectMetricsConfig(metric_type=ProjectMetricType.DURATION)        # "DURATION"
```

**`TRACE_DURATION` does not exist.** The `ProjectMetricType` value for trace duration charts is `DURATION` (bare). `THREAD_DURATION` and `SPAN_DURATION` exist for their respective entity types.

---

## How to recover when a hint fires

The `opik_sdk` tool appends a `[hint]` line to failed runs when the failure matches one of the patterns above. When you see it:

1. Read the hint — it names the exact wrong → right substitution.
2. If the hint suggests reloading a reference, do so before rewriting.
3. Rewrite the script with the correct method/field/import.
4. Re-run.

Do not retry the same broken pattern with a different argument — the resource/method/import doesn't exist, so no argument shape can salvage it.
