---
kind: backlog
flow: explain-trace
symptom: "the spanBodies note says lost bodies are 'which the same call returns', names no call that returns them, and sits next to 'no span reached the cut'"
decided: 2026-09-23, found by /dogfood, OPIK-8485
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
The note should name the call that fetches a span whole, and not claim both a cut and no cut.
