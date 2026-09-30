---
kind: context
flow: spend
symptom: "Claude Code tool spans are named `tool_use: <tool>` without the MCP server"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/read_list/entities/spend/user.py
---
Only the spend types (a lane read, then the who-uses filter) can attribute usage to an MCP server.
