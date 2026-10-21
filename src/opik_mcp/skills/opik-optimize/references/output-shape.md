---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# Output shape

- `status`: `improved` | `no_improvement` | `blocked`
- `prompt`: `name`, `source` (`library` | `code` | `trace`), `baseline_version`, `new_version` (when saved)
- `dataset`: `train` `{name, count}`, `validation` `{name, count}`
- `metric`: `name`, `kind` (`heuristic` | `judge`), `validated: true|false`
- `optimizer`: `algorithm`, `n_samples`, `max_trials`, `stop_reason`
- `scores`: `initial`, `optimized`, `validation`, `delta`
- `cost`: `llm_calls`, `llm_cost_total`
- `run_url`
- `next_step`: exactly one

Invariants: `improved` requires `scores.validation > scores.initial` beyond noise and carries a `run_url`; `no_improvement` still carries the `run_url` and the cost; a `judge` metric with `validated: false` is flagged in the report; the codebase is never modified; a library prompt is saved as a **new version**, never overwritten in place.
