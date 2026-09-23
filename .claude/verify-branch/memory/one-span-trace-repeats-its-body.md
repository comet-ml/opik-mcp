---
kind: backlog
flow: explain-trace
symptom: "read('trace') on a one-span trace repeats the trace's input, output and error_info (traceback included) in the span"
decided: 2026-09-23, found by /verify-branch, OPIK-8485
recheck_when: src/opik_mcp/read_list/entities/trace.py
---
About half the answer is the copy. The span could point to the trace's fields instead of repeating them.
