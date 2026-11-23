---
kind: backlog
flow: traces
symptom: "the default list('trace') page has no feedback_scores column, even when every row has a score"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
Same on main. The fields: line offers it, so one more call gets it.
