---
kind: backlog
flow: compare-experiments
symptom: "list('dataset_item', experiment_ids=[…]) returns no UI link, though each experiment row in list('experiment') has a compare url"
decided: 2026-11-23, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/entities/dataset/compare.py
---
Same on main. The per-case answer could carry the compare view link.
