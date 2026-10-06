---
kind: backlog
flow: compare-experiments
symptom: "list('dataset_item', experiment_ids=[…]) shows a case as errored under the score column shown, although that score was 1.0 in the trace and a different metric raised"
decided: 2026-10-06, found by /dogfood, OPIK-8562
recheck_when: src/opik_mcp/read_list/entities/dataset/compared_row.py
---
The cell should name the metric that failed, or the agent blames the wrong judge.
