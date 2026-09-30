---
kind: context
flow: spend
symptom: "list('spend_summary') shows over-plan and API dollars as n/a and the billed total as n/a, while every list('spend_user') row shows $0.00 for both"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/summary.py
---
The summary endpoint returns null for those two fields when nothing was metered and the users endpoint returns 0; the answers show what each endpoint said.
