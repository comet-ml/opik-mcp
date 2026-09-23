---
kind: backlog
flow: all list flows
symptom: "list answers carry no token-size header; read answers do (`| 694 tok |`)"
decided: 2026-09-23, found by /verify-branch, OPIK-8485
recheck_when: src/opik_mcp/read_list/list_tool.py
---
A caller can't see what a page costs before asking for the next one. Worth a header like read's.
