---
kind: backlog
flow: compare-experiments
symptom: "when every run in a compare ties, worst_trace still names one run's trace as worst on every row"
decided: 2026-10-06, found by /dogfood, OPIK-8562
recheck_when: src/opik_mcp/read_list/entities/dataset/compared_row.py
---
On a tie it should say the runs tied rather than pick one.
