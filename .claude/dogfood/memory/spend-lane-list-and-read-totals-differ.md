---
kind: context
flow: spend
symptom: "list('spend_lane') and read('spend_lane', key) give different tokens and list-price dollars for the same lane and window (about 3% on the largest lane)"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/lane.py
---
They come from two backend views (composition and lane breakdown), and the AI Spend pages built on them show the same gap; it is not an MCP bug.
