---
kind: backlog
flow: explain-trace
symptom: "read('trace') on a Claude Code trace inlines the session's full settings in metadata: about 5,800 tokens, including permission lists and a working directory with a username"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
Same on main. The settings rarely answer a question; slimming them would cut most of a turn read's cost.
