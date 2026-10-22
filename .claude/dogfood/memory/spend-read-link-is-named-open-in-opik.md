---
kind: backlog
flow: spend
symptom: "read('spend_lane') and read('spend_session') headers say to name the link 'Open in Opik' although the URL opens an AI Spend page"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/lane.py
---
The header text comes from the shared read path; the spend reads already set url_opens with the page's own name.
