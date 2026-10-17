---
last_updated: "2026-09-17"
source_commit: "2.0.0"
---

# Output shape

- `status`: `ship` | `hold` | `needs_review` | `insufficient_evidence` | `blocked`
- `policy`: `source` (`file` | `defaults`), `path`, `values` (the effective policy)
- `suite`: `name`, `id`, `version`
- `baseline` / `candidate`: `experiment_id`, `name`, `url`, `items`, `pass_rate`
- `criteria`: list of `{name, threshold, observed, passed, note}` — always all of them
- `regressions`: list of `{dataset_item_id, input, assertion, reason, trace_url, safety: bool, flaky: bool}`
- `flaky`: list of `{dataset_item_id, baseline_runs, candidate_runs}` excluded or counted per `flaky_policy`, or the string `not_evaluated` on a single-run suite
- `review_items`: list of `{dataset_item_id, why}` (when `needs_review`)
- `evidence`: `{items, fixes, regressions, sign_test_p}`
- `compare_url`
- `recorded`: `true|false`
- `next_step`: exactly one

Invariants: `ship` requires every gate criterion `passed` **and** `judge_validated: true`; `hold` carries at least one failed criterion and, when the failure is regressions, a non-empty `regressions`; `insufficient_evidence` carries `criteria` with `min_items` failed; `needs_review` carries a non-empty `review_items` or `judge_validated: false` in `policy.values`; `criteria` is never partial; the codebase is never modified; nothing is deployed.
