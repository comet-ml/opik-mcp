---
kind: backlog
flow: spend
symptom: "spend_session's turns (78 for one session) is lower than the non-automated traces the outline call returns (82), far below the thread's messages, and above the real prompts, with no definition given"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/session.py
---
The figure is the backend's turn count; the answer could say what it counts, or the guide could.
