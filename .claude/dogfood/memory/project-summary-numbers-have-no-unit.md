---
kind: backlog
flow: project-health
symptom: "read('project') shows errors: 18.07 and avg_duration: 3.57 with no unit; they are a percent and milliseconds"
decided: 2026-09-23, found by /dogfood, OPIK-8485
recheck_when: src/opik_mcp/read_list/entities/project/summary.py
---
A percent reads as a count of errors.
