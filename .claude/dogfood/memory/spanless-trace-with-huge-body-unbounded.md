---
kind: backlog
flow: explain-trace
symptom: "read('trace') on a trace with no spans and a body of about 2 MB returns about 820,000 tokens with no warning"
decided: 2026-10-26, found by /dogfood, OPIK-8951
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
Same on main. Only span bodies are slimmed, and nothing in the list row hints at the size; see the open item in ADR 0002.
