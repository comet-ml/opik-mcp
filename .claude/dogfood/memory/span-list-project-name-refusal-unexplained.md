---
kind: backlog
flow: traces
symptom: "list('span', ..., fields=['project_name']) gives a plain 'Unknown field', though read('span') returns project_name"
decided: 2026-11-24, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/entities/span.py
---
Refused on purpose: the span list endpoint never sends it. The refusal could say so and point to read('span') or project_id.
