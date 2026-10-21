---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# Examples

**Fix verified.** `/opik-compare support-bot-regressions` after fixing `retrieve()`. Baseline = yesterday's run (3/5 passed). Runner built from `Entrypoint: answer`; candidate 5/5. Read back: 2 fixes (the refund and shipping items), 0 regressions, `pass_rate 0.60 → 1.00 (+0.40)`, same suite version, same judge. → **`compared`**; next step = "run `/opik-online-eval` to watch the refund assertion in production" or "merge — the decision is yours".

**Regression surfaced.** Candidate fixes the refund case but the "does not give legal advice" item flips to fail; its `reason` quotes the new output offering legal steps. Attribution: app. → **`compared`** with one regression named first, the delta table after; next step = "`/opik-explain <candidate trace>` for the legal-advice item".

**First run.** No prior experiments on the suite. Run once; report 3/5 with the failing items. → **`baseline_created`**: "This is the baseline — rerun `/opik-compare` after your change."

**Two existing runs, no code.** `/opik-compare nightly-2026-09-14 nightly-2026-09-15`. Resolve by `get_experiments_by_name`, join items, report. → **`compared`** (no runner built).
