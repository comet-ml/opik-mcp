---
kind: backlog
flow: traces
symptom: "a list cell gives end_time to the second while the footer says values are uncut; read gives microseconds"
decided: 2026-11-24, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/list_table.py
---
Same on main. Either keep the full timestamp under fields= or say the cell is rounded.
