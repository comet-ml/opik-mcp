---
kind: backlog
flow: traces
symptom: "in a feedback_scores cell, the order of the scores changes from row to row"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/list_table.py
---
Same on main; the cell keeps the backend's order. Sorting by name would let rows be compared by eye, but it changes every score cell.
