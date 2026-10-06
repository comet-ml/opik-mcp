---
kind: backlog
flow: project-health
symptom: "read('project') on a project with no experiments or datasets has no contains key, though the tool description promises {project, summary, vocabulary, contains, url}"
decided: 2026-10-06, found by /dogfood, OPIK-8562
recheck_when: src/opik_mcp/read_list/entities/project/read.py
---
Say the project has nothing fresh instead of dropping the key.
