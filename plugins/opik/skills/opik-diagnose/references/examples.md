---
last_updated: "2026-09-09"
source_commit: "2.0.0"
---

# Examples

**Triage a project, MCP connected.** `/opik-diagnose`. `list('agent_insights_issue', project_name=…)` returns two open issues: a high-severity tool-call loop (12 occurrences yesterday) and a low-severity empty-answer issue. `read` on the first gives the cause, three example trace ids and the links. `list('trace', since="24h", sort="duration desc")` then finds one trace 5x the p90 that no issue covers. Shortlist = the tool-call loop (rank 1, `diagnostics`, first example trace), the latency outlier (2, `latency`), the empty-answer issue (3, `diagnostics`); next step = "explain the top trace". `source=mcp`. → **`found`**.

**Triage a project, SDK only.** `/opik-diagnose`. `find_agent_insights_issues` returns nothing yet; `search_traces` on the project finds two errored traces, one 5x the p90 duration, one scored 0.2 on Hallucination. Shortlist = the two errors (rank 1-2), the latency outlier (3), the low-score trace (4), each with its signal; next step = "explain the top trace". `source=sdk`. → **`found`**.

**Nothing wrong.** `/opik-diagnose`. Reads fine, but no trace errored, ran slow, or scored low. → **`empty`**: "No traces crossed a threshold in the recent window."

**Blocked — no config.** `/opik-diagnose`. No `~/.opik.config`, no `OPIK_API_KEY`. → **`blocked`**: "run `opik configure`, then rerun `/opik-diagnose`." (No code touched.)
