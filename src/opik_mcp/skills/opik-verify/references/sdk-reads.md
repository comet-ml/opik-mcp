---
last_updated: "2026-09-17"
source_commit: "2.0.0"
---

# SDK reads and writes

## Resolve the two runs

```python
import opik

client = opik.Opik()
runs = sorted(
    client.get_test_suite_experiments(name="<suite>", project_name="<project>"),
    key=lambda e: e.get_experiment_data().created_at,
)
baseline, candidate = runs[-2], runs[-1]  # or the two ids the user gave
```

## Read both runs

```python
from collections import defaultdict


def by_item(exp):
    groups = defaultdict(list)
    for i in exp.get_items():
        groups[i.dataset_item_id].append(i)  # each: dataset_item_data (tags / subgroup key),
    return groups  #       assertion_results [{passed, reason}], trace_id


b, c = by_item(baseline), by_item(candidate)


def run_passed(i):
    return bool(i.assertion_results) and all(a.get("passed") for a in i.assertion_results)


def counts(runs):
    return sum(run_passed(r) for r in runs), len(runs)  # runs_passed, runs_total


thresholds = {
    it["id"]: (it.get("execution_policy") or suite.get_global_execution_policy() or {}).get(
        "pass_threshold", 1
    )
    for it in suite.get_items()
}  # the suite, not the experiment, holds the policy


def passed(item_id, runs):
    return counts(runs)[0] >= thresholds.get(item_id, 1)


# experiment level (client.rest_client.experiments.get_experiment_by_id(id)): pass_rate,
#            duration (p50/p90/p99), total_estimated_cost_avg, dataset_version_id
```

## Record the verdict

```python
exp = client.rest_client.experiments.get_experiment_by_id(candidate.id)
cfg = dict(exp.metadata or {})
cfg["verdict"] = {
    "status": "hold",
    "policy": "opik-release-policy.yaml",
    "failed": ["regressions"],
    "at": "<iso time>",
}
client.update_experiment(id=candidate.id, experiment_config=cfg)
```
