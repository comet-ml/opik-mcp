---
last_updated: "2026-09-17"
source_commit: "2.0.0"
---

# Release policy keys and defaults

```yaml
# opik-release-policy.yaml — repo root or .opik/. Versioned with the code so the gate is reproducible.
min_items: 10                      # fewer scored items than this -> insufficient_evidence, never ship
max_regressions: 0                 # pass -> fail cases allowed (flaky items excluded when flaky_policy: exclude)
pass_rate: not_below_baseline      # or a number in 0..1; candidate pass rate must satisfy it
safety_tags: [safety]              # a regression on an item whose data.tags contains one of these -> hold, always
subgroup_key: null                 # a data key (e.g. "category"); no subgroup's pass rate may fall
latency_p90_max_increase: 0.25     # candidate p90 duration vs baseline (experiments expose p50/p90/p99)
cost_per_item_max_increase: 0.25   # candidate mean cost per item vs baseline, as a fraction
flaky_policy: exclude              # exclude | count — an item that flips between runs of the SAME code is flaky
judge_validated: false             # set true once the suite's judge has been checked against human labels
```
