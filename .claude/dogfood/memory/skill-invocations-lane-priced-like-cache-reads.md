---
kind: context
flow: spend
symptom: "list('spend_lane') shows the skill_invocations output lane at about $0.70 per million tokens, which is a cache-read rate, not an output rate"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/lane.py
---
The figure comes from the backend's composition view; check its pricing before quoting that lane as output spend.
