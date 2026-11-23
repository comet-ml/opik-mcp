---
kind: backlog
flow: project-health
symptom: "list('project') has no sort by latest trace, so 'the project with the most recent traces' is found by eye"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/project/__init__.py
---
Check whether the backend's project list can sort by last trace time before adding it.
