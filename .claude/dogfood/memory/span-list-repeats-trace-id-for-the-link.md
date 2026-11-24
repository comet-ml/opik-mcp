---
kind: backlog
flow: traces
symptom: "list('span', filters='trace_id = \"…\"', fields=[…]) repeats the same trace_id on every row for the link, about 40% of the answer, and does not say why the column is there"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/list_table.py
---
The column fills the row link's {trace_id} slot. When a filter pins one trace, the header could state it once instead, and an added column could say "added for the row link".
