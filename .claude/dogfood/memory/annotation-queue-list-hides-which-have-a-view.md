---
kind: backlog
flow: annotation-queues
symptom: "list('annotation_queue') has no column saying whether a queue has a custom annotator view"
decided: 2026-10-26, found by /dogfood, OPIK-8951
recheck_when: src/opik_mcp/read_list/entities/annotation_queue.py
---
The backend's list rows never carry the view, so finding the queues with one takes a read per queue.
