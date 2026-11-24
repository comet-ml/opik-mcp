---
kind: backlog
flow: explain-trace
symptom: "read('span', id, fields=['metadata.gen_ai.request.model']) says 'not set' and returns {'metadata': {'gen_ai': null}} when the key 'gen_ai.request.model' has a value"
decided: 2026-11-24, found by /dogfood review, OPIK-8948
recheck_when: src/opik_mcp/read_list/projection.py
---
check() accepts the path from the record's own keys, then dig() splits on every dot and misses the key; a score name with a dot fails the same way. Main dropped the field without a word, so this blocks PR #277 until fixed.
