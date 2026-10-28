---
kind: backlog
flow: traces
symptom: "list('trace'|'thread') shows the annotation_queue_ids column blank on every row, and fields=['annotation_queues'] prints each queue as '<name>=None'"
decided: 2026-10-26, found by /dogfood, OPIK-8951
recheck_when: src/opik_mcp/read_list/list_table.py
---
Same on main. A list of queue objects is flattened as if it were a mapping; the cell should name the queues.
