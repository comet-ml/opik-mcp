---
kind: backlog
flow: traces
symptom: "list('span', fields=[…]) drops trace_id, which the span list's url_template needs"
decided: 2026-09-29, found by /dogfood, NA; narrowed 2026-11-23: names under fields=['name'] are no longer cut
recheck_when: src/opik_mcp/read_list/list_table.py
---
Same on main. Either keep the link key under fields= or drop the template.
