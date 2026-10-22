---
kind: backlog
flow: traces
symptom: "list(..., fields=['name']) promises uncut values but still cuts names with '…', and a span list's url_template needs {trace_id}, which that projection drops"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/list_table.py
---
Same on main. Either keep the link key under fields= or drop the template.
