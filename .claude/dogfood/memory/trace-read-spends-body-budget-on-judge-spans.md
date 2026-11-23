---
kind: backlog
flow: explain-trace
symptom: "read('trace') spends the inline body budget in span order, so a judge's long prompt keeps its body while the agent's own LLM and tool spans lose theirs"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
The answer says which bodies it dropped; the order is the question.
