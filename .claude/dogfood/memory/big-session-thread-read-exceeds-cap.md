---
kind: context
flow: spend
symptom: "read('thread') on a session of about 500 turns is about 27,000 tokens on both builds, over the host's result cap"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/thread.py
---
Spend flows outline a session with list('trace', fields=['name']) instead, as the guide and the spend_session read say.
