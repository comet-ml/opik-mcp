---
kind: backlog
flow: traces
symptom: "list('trace'|'thread', filters='annotation_queue_ids …') adds an annotation_queue_ids column that is blank on every row"
decided: 2026-10-26, found by /dogfood, OPIK-8951; narrowed 2026-11-23, re-checked: same
recheck_when: src/opik_mcp/read_list/entities/trace.py, src/opik_mcp/read_list/entities/thread.py
---
The rows carry `annotation_queues` (objects with `id` and `name`), not `annotation_queue_ids`; a derived column like trace's `experiment_id` would fill it. The `<name>=None` cell under `fields=["annotation_queues"]` is fixed.
