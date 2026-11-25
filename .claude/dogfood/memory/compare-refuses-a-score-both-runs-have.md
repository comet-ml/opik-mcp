---
kind: backlog
flow: compare-experiments
symptom: "list('dataset_item', experiment_ids=[a, b], fields=['feedback_scores.<score>']) is refused, and the default table drops some score columns without naming them"
decided: 2026-11-24, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/entities/dataset
---
Same on main. Compare mode builds its fields from the page and its chosen columns, not from the experiments' score names.
