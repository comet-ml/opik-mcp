---
last_updated: "2026-09-09"
source_commit: "2.0.0"
---

# Output shape

- `status`: `found` | `empty` | `blocked`
- `scope`: `project`, `window`
- `shortlist`: list of `{trace_id, trace_url (the Opik UI link), signal (error|tool_call|latency|low_score|regression|diagnostics), why, rank}`
- `source`: `sdk` | `mcp` | `diagnostics_pending` (a scan was triggered this run; the shortlist comes from traces)
- `next_step`: exactly one (typically "explain the top trace")

Invariants: `found` carries a non-empty `shortlist`, each item with a `signal`, a `trace_id`, and a clickable `trace_url`; `empty` = the read succeeded but nothing crossed a threshold; `blocked` carries exactly one `next_step`; the shortlist never contains offline experiment results; every path leaves the codebase unchanged.
