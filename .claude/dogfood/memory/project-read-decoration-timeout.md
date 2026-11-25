---
kind: context
flow: project-health
symptom: "read('project') can come back without experiment_metadata_keys when that backend call takes over 2.5 s; a retry returns it"
decided: 2026-11-23, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/decorations.py
---
A one-off backend delay, not a difference between builds; compare sizes only after a retry.
