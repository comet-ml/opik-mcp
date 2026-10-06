---
kind: backlog
flow: explain-trace
symptom: "read('trace') on an errored 2- to 5-span trace repeats the same error_info traceback on the trace and on every span that re-raised it, about two thirds of a 5,600-token answer"
decided: 2026-10-06, found by /dogfood, OPIK-8562
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
Same cause as the one-span case, but deeper trees repeat it more. Inline the traceback once and point to it from the spans.
