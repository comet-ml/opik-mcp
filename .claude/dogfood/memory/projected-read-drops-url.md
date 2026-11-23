---
kind: backlog
flow: explain-trace
symptom: "read(…, fields=[…]) drops the url, so a projected answer cannot link to the record in the UI"
decided: 2026-11-23, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/read_tool.py
---
Same on main. The id is kept; the url could be kept the same way.
