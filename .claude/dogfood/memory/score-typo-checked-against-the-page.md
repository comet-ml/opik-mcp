---
kind: context
flow: traces
symptom: "on a list, a misspelt score name comes back 'not set' when no row on the page holds the real score"
decided: 2026-11-23, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/projection.py
---
Not false data: when a row holds the real score the typo is refused with it, and when none does the real score is not set on those rows either.
