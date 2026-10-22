---
kind: context
flow: spend
symptom: "the top rows of list('spend_user') all show the same billed dollars (one seat price each), so billed money cannot rank them"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/user.py
---
The ranking is by tokens and the answer says so; quote billed dollars as a per-seat figure, not as a rank.
