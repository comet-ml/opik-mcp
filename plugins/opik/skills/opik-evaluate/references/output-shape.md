---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# Output shape

- `status`: `evaluated` | `blocked`
- `shape`: `test_suite` | `dataset` | `server_side`
- `cases`: `name`, `id`, `count`, `source` (`traces` | `provided` | `synthetic` | `existing`)
- `scoring`: list of `{name, kind: heuristic|judge|assertion, failure_mode}`
- `experiment`: `id`, `name`, `url`, `project`
- `scores`: list of `{metric, value}` (pass rate included)
- `worst`: list of `{dataset_item_id, input, score_or_assertion, reason, trace_url}`
- `next_step`: exactly one

Invariants: `evaluated` carries an `experiment.url` and non-empty `scores`; each judge in `scoring` names one `failure_mode`; `blocked` carries exactly one `next_step`; every path leaves the codebase unchanged.
