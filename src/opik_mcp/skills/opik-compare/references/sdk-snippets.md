---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# SDK snippets

## Resolve the suite and the baseline

```python
import opik

client = opik.Opik()

suite = client.get_test_suite(name="<suite>", project_name="<project>")
prior = client.get_test_suite_experiments(
    name="<suite>", project_name="<project>"
)  # newest first is not guaranteed — sort by created_at yourself
```

## Prompt candidates

```python
client.rest_client.experiments.execute_experiment(
    dataset_name=suite.name,
    dataset_id=suite.id,
    prompts=[
        {
            "model": "<model>",
            "messages": [...],
            "configs": {},
            "prompt_versions": [{"id": "<prompt_version_id>"}],
        }
    ],
    project_name="<project>",
)  # 202 Accepted; one experiment per prompt variant, processed asynchronously — poll get_experiment_by_id until items fill
```

## Run the candidate

```python
result = opik.run_tests(
    test_suite=suite,  # or suite.get_version_view("<baseline's version>") to pin
    task=task,
    experiment_name="candidate-<sha>",
    experiment_tags=["compare", "<sha>"],
    model="<same judge model as baseline>",
    generate_report=False,  # default True writes opik_test_suite_reports/ into cwd — the repo stays untouched
)
candidate_id = result.experiment_id  # result.experiment_url is the single-run link

# GUARD: a missing judge credential does NOT raise — every item comes back failed with
# scoring_failed=True and a "Missing credentials" reason, and the experiment is still created.
# Treat that as a Blocker, not as a regression; do not compare against that run.
judge_failed = [
    r
    for ir in result.item_results.values()
    for t in ir.test_results
    for r in t.score_results
    if getattr(r, "scoring_failed", False)
]
```

## Read both runs back

```python
base = client.get_experiment_by_id("<baseline_id>")
cand = client.get_experiment_by_id(candidate_id)
b_items = {i.dataset_item_id: i for i in base.get_items()}
c_items = {i.dataset_item_id: i for i in cand.get_items()}
# each item: dataset_item_data, evaluation_task_output, feedback_scores [{name, value, reason}],
#            assertion_results [{value, passed, reason}], trace_id

# Side by side, one call, both experiments:
rows = client.get_experiments_client().find_experiment_items_for_dataset(
    suite.name, experiment_ids=["<baseline_id>", candidate_id], project_name="<project>"
)
```
