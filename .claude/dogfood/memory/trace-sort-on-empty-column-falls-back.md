---
kind: backlog
flow: traces
symptom: "list('trace', sort='usage.total_tokens desc') on traces whose usage is empty comes back in time order without saying so"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/sorting.py
---
Same on main. The answer should say the sort field had no values.
