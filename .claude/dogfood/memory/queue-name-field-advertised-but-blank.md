---
kind: backlog
flow: traces
symptom: "the fields: line offers annotation_queues.<queue name>, and fields=['annotation_queues.<queue name>'] is blank on every row, also on rows in that queue"
decided: 2026-11-23, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/projection.py
---
Same on main. A named entry with no value resolves to nothing; either fill it or stop offering the path.
