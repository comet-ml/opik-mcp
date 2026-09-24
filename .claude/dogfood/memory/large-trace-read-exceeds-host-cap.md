---
kind: backlog
flow: explain-trace
symptom: "read('trace') on a 250-span trace returns about 38,000 tokens even with bodies slimmed; Claude Code rejects results over 25,000"
decided: 2026-09-23, found by /dogfood, OPIK-8485
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
The span skeletons carry the weight (ids, audit fields, source, environment). The customer sees no answer at all. See the open item in ADR 0002.
