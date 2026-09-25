---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# SDK snippets

## Build the cases

```python
import opik

client = opik.Opik()

suite = client.get_or_create_test_suite(
    name="<project>-eval",
    project_name="<project>",
    global_assertions=["<one behavior every answer must show>"],  # optional
)
suite.insert(
    [
        {
            "data": {"input": t.input, "source_trace_id": t.id},
            "assertions": ["<what a correct output does>"],
        }
        for t in sampled_traces
    ]
)
```

## Run it

```python
# Test suite
results = opik.run_tests(
    test_suite=suite,
    task=lambda item: {"input": item["input"], "output": str(app(item["input"]))},
    experiment_name="baseline-<sha>",
    model="<judge model>",
    generate_report=False,
)  # default True writes opik_test_suite_reports/ into cwd — keep the repo clean
# Dataset
from opik.evaluation import evaluate

res = evaluate(
    dataset=dataset,
    task=task,
    scoring_metrics=[...],
    experiment_name="baseline-<sha>",
    scoring_key_mapping={"reference": "expected_output"},
)  # map dataset keys onto metric args
```

## Read the scores back

```python
exp = client.get_experiment_by_id(results.experiment_id)  # or res.experiment_id
items = exp.get_items()  # dataset_item_data, evaluation_task_output, feedback_scores [{name,value,reason}], assertion_results [{passed,reason}], trace_id
# aggregate: mean per score name; pass rate = items where every assertion passed / items with assertions
```
