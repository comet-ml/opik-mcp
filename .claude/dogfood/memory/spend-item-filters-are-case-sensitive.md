---
kind: backlog
flow: spend
symptom: "list('spend_user', filters='mcp_server = \"jira\"') finds no users when the server is logged as \"Jira\", with no near-match hint"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/user.py
---
The who-uses endpoints match the item name exactly; the answer could name the items a lane read shows.
