---
kind: backlog
flow: compare-experiments
symptom: "list('experiment', size=5) page 1 leaves out newer experiments that size=15 lists first"
decided: 2026-11-23, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/entities/experiment.py
---
Same on main, so probably the backend's paging; check the endpoint before changing the server.
