---
kind: backlog
flow: compare-experiments
symptom: "list('dataset_item', experiment_ids=[a, b]) gives no UI link to the compare view of both experiments"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/dataset/__init__.py
---
The experiment list already builds a compare link for one experiment; the two-run page takes both ids.
