---
kind: backlog
flow: compare-experiments
symptom: "list('experiment') rows for experiments with 0 traces have an empty url with no reason given"
decided: 2026-10-06, found by /dogfood, OPIK-8562
recheck_when: src/opik_mcp/read_list/entities/experiment.py
---
Either link the experiment page or say why there is no link.
