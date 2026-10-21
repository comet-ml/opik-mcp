---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# Output shape

- `status`: `compared` | `baseline_created` | `blocked`
- `suite`: `name`, `id`, `version`
- `baseline` / `candidate`: `experiment_id`, `name`, `url`, `items`, `pass_rate`
- `comparable`: `true|false`, `note`
- `deltas`: list of `{metric, baseline, candidate, delta}` (pass rate included)
- `regressions`: list of `{dataset_item_id, input, assertion, reason, trace_url}`
- `fixes`: same shape
- `attribution`: `{app: [...ids], judge: [...ids]}` when a flip could be attributed
- `compare_url`
- `next_step`: exactly one
- `verdict`: **never present**

Invariants: `compared` carries both experiments, a non-empty `deltas`, and a `compare_url` with both ids; `baseline_created` carries `baseline` only and a `next_step` of "rerun after the change"; `blocked` carries exactly one `next_step`; no path writes into the codebase; no path emits a ship/hold decision.
