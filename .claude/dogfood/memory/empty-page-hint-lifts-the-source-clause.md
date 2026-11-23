---
kind: backlog
flow: traces
symptom: "an empty filtered trace page says 'Without your filter this listing has N', and N also drops the caller's own source = … clause, so it counts every source"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/list_empty_page.py
---
Same on main. Keep an explicit source clause when the hint counts the rest.
