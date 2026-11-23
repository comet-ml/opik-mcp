---
kind: context
flow: threads
symptom: "list('thread', fields=['annotation_queues']) says Unknown field when no thread on the page is in a queue"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/thread.py
---
Thread records carry annotation_queues only when a thread-scoped queue holds them; to test thread queue cells, find or seed such a queue first.
