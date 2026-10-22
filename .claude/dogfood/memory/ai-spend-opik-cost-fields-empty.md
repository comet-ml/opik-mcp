---
kind: context
flow: spend
symptom: "in an AI Spend workspace, Opik's usage.total_tokens, span_token_usage and cost fields are empty or $0"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/
---
Only the spend_* types carry tokens and dollars there, so a base build can't rank spend at all and a token sort on traces falls back to time order.
