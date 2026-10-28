---
kind: context
flow: annotation-queues
symptom: "listing a queue's traces gives the same rows on a build with and without the sdk default, because every queued trace in the local workspace is from the SDK"
decided: 2026-10-26, found by /dogfood, OPIK-8951
recheck_when: src/opik_mcp/read_list/oql_fields.py
---
Only the filter echo line differs; showing the difference needs a queue holding playground or experiment traces.
