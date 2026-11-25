---
kind: accepted
flow: traces
symptom: "fields=['feedback_scores.<typo>'] or ['metadata.<key no record holds>'] comes back null and 'not set' instead of a refusal, on a list and on a read"
decided: 2026-11-24, jverre, OPIK-8948
recheck_when: src/opik_mcp/read_list/projection.py
---
Fail open: the key is not set on those records, and the marker says so. A typo close to a key the records hold is still refused with that key.
