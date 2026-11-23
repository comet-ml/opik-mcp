---
kind: backlog
flow: traces
symptom: "fields=['parent_spanid'] or ['trace.outptu'] is refused without 'Did you mean', and the right field can sit in the '+N more' tail"
decided: 2026-11-23, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/projection.py
---
Same on main. Only score and other user keys get a suggestion; fixed fields could use the same close-match check.
