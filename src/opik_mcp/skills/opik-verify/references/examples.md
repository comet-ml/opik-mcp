---
last_updated: "2026-09-17"
source_commit: "2.0.0"
---

# Examples

**Ship.** `/opik-verify` after compare: 24 scored items, 3 fixes, 0 regressions, pass rate 0.79 → 0.92, p90 latency +4%, cost +2%, sign test p = 0.25 ("too few flips to be more than noise — but nothing regressed"), policy file present with `judge_validated: true`. → **`ship`**; next step = "merge; `/opik-online-eval` watches the refund assertion in production".

**Hold.** Same, but the "does not give legal advice" item flipped pass → fail and is tagged `safety`. Regressions 1 > 0 and safety fail. → **`hold`**, that case named first with its reason and trace link; next step = "`/opik-explain <trace>` for the legal-advice item".

**Needs review.** All gates pass, no policy file (defaults), so `judge_validated` is false. → **`needs_review`**: "20 items pass the defaults; a person should check 5 judge decisions (linked) and then set `judge_validated: true` in `opik-release-policy.yaml` — want me to write the file with the defaults?"

**Insufficient evidence.** A two-item suite, both fixed, nothing regressed. → **`insufficient_evidence`**: "2 items is below `min_items: 10` — add cases with `/opik-test` or lower `min_items` in the policy (your call, and it will be visible in the file)."
