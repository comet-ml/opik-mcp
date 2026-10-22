---
kind: backlog
flow: spend
symptom: "read('spend_session', <id>) with no narrative shows user, start, duration, turns and tokens but not the one-line summary that list('spend_session') shows for the same row"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/session.py
---
The narrative endpoint's session object carries no summary; adding it means one sessions lookup on the not-ready path.
