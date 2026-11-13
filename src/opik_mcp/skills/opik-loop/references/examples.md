---
last_updated: "2026-11-13"
source_commit: "3.0.6"
---

# Examples

**Set up after instrument.** `/opik-loop` on `support-agent`, traces landing, a `resolution` judge already live. `list('recommendation')` ranks Collect first: "Nothing adds production failures to a test suite. 1,240 traces scored resolution in 14 days, 189 below 0.5." Dry run of `workflow.enable` with `recipe: "collect"` shows one automation, "Low-resolution traces to the suite": `feedback_score:created` where `resolution < 0.5` → `test_suite.add` into `Regression · support-agent` (created), estimate `fired 1,240, items 189, cost $0`. User says yes; the live call creates it; `list('automation')` shows it enabled. → **`on`**, `check_in_suggested: true`; next step = "in a week, ask what changed in the loop".

**Next step applied.** `/opik-loop what should I do next` on a project with Collect on for 20 days. Top recommendation is upkeep: "Collect added 310 cases in 14 days", `apply_call` = `write('automation.update', data={"automation_id": "…", "limits": {"max_items_per_day": 100}})`. Dry run shows the estimate at the new cap; user says yes. → **`applied`**; next step = "check the suite's growth next week".

**Worth it, answered from the stats.** `/opik-loop is Collect still worth it`. `read('automation', id)`: `stats` = fired 2,100, sampled 2,100, items_written 44, cost $0; `paused_reason: null`; no recommendation names it. Answer: 44 cases a week into the suite at no cost, nothing to change. → **`explained`**; next step = "run `/opik-compare` on the suite before the next prompt change".

**Failed run explained.** `/opik-loop why did this automation fail`. `list('automation_run', automation_id=…, status='failed')` → one hourly rollup; `read('automation_run', id)`: step `keep` failed 12 of 12 with `last_error: "suite not found"`, `parent_run_id: null`. The suite was deleted. → **`explained`**; next step = "re-point the step at an existing suite with `automation.update`, or recreate it with `/opik-test`".

**Custom automation, tested first.** `/opik-loop send refund answers below 0.5 to review`. Dry run of `automation.create`: trigger `feedback_score:created` on `resolution < 0.5` with `per_day: 10`, step `annotation_queue.add` into `Refund review` (created), limits none needed (no step costs money), estimate `items 10/day`. `automation.test` on a recent refund trace returns step `add` as `done`. User says yes. → **`created`**; next step = "annotate the first few in the queue, then `/opik-loop` again for Align".

**Blocked — no backend.** `list('recommendation')` is refused: this Opik has no automations yet. → **`blocked`**: "it needs a backend version that ships automations; `list('online_rule', …)` still shows the judges." (Nothing written.)
