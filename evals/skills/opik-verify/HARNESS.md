# `/opik-verify` evals

Test cases, automation, and success metrics for the `opik-verify` skill — the ship/hold
verdict OPIK-7470 deferred until compare produced real deltas.

## Layout

```
evals/
  cases.yaml                          # triggering + functional (ship, hold, safety) + edge (review, too_small, too_small_hold)
  fixtures/gate/seed.py               # ONE suite (12 items), baseline + candidate-ship / -hold / -safety
  fixtures/gate/opik-release-policy.yaml   # the policy; `prepare` rewrites judge_validated / min_items per case
  grader.py                           # deterministic: verdict, criteria completeness, regressions by id, read-only
  metrics.py                          # aggregate -> the metrics below
  run_evals.py                        # prepare (seed once, stage 4 workdirs) / grade (offline) / trigger-*
  _work/                              # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). `prepare` needs **Opik configured**, network,
and a judge model for the suite's assertions — `OPIK_EVAL_JUDGE_MODEL` (default
`gpt-4o-mini`) with its provider key. The skill itself only reads. Grading is offline.

```bash
uv run --with pyyaml python run_evals.py prepare
#  ... run /opik-verify with cwd = _work/<case>/ for ship, hold, review, too_small; prompt in PROMPT.txt.
#      Each should write result.json = {status, policy, suite, baseline, candidate, criteria,
#      regressions, review_items, evidence, compare_url, next_step}.
uv run --with pyyaml python run_evals.py grade
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare
uv run --with pyyaml python run_evals.py trigger-grade
```
The decoy that matters is `opik-compare`: "did my fix work" is compare, "is this safe to
ship" is verify. The compare harness carries the mirror-image negatives.

## What the cases check

The seeder plants one suite and three runs whose per-item outcomes are known:

| item group (n) | baseline | candidate-ship | candidate-hold | candidate-safety |
|---|---|---|---|---|
| refund (4) | 1 pass | pass | pass | pass |
| shipping (4) | 1 pass | pass | pass | pass |
| hours (2) | pass | pass | **FAIL** (regression) | pass |
| legal (2, tagged `safety`) | pass | pass | pass | **FAIL** (safety regression) |

- **ship** — `judge_validated: true`; every gate passes → `ship`, empty regressions, both ids in the link, `policy.source = file`.
- **hold** — the two hours items are named under `regressions` by item id, nothing else is, `regressions` failed, and the `subgroups` criterion shows hours going 1.00 → 0.00 (the arithmetic, not just "passed").
- **safety** — the two legal items regress, each flagged `safety: true`, `safety` and `regressions` both failed → `hold`. The only candidate that can fail the criterion with the strongest wording in the skill.
- **review** — same numbers as ship, `judge_validated: false` → `needs_review`, not `ship`.
- **too_small** — `min_items: 50` → `insufficient_evidence` with `min_items` failed, even though nothing regressed.
- **too_small_hold** — the hold candidate under `min_items: 50` → `hold`, not `insufficient_evidence`: a failed gate outranks thin evidence.

Every case also requires the **full criteria table** (each policy-driven criterion present) and an unchanged workdir.

## Metrics

`selection_accuracy` · `verdict_accuracy` · `criteria_completeness` · `regression_recall` ·
`regression_precision` · `arithmetic_shown` · `gate_integrity` (ship only with all gates + validated judge; hold only with a failed gate) ·
`read_only_rate` (target 1.0) · `schema_compliance`.

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack. Matches the other harnesses.
