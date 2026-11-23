---
kind: backlog
flow: compare-experiments
symptom: "in a dataset_item comparison, the runs' output keys list comes back in a different order on each call"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/dataset/compare_notes.py
---
Same on main. Sort the keys so two answers can be compared.
