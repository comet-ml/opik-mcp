# `/opik-evaluate` evals

Triggering suite for the `opik-evaluate` skill, plus the pinned functional contract
(OPIK-7646). The functional fixture and grader are the next step; the contract is
here so they can be added without moving the target.

## Layout

```
evals/
  cases.yaml     # triggering (automated) + functional contract (fixture TODO)
  run_evals.py   # trigger-prepare / trigger-grade
  _work/         # judge input + verdicts (gitignored)
```

## Run it

```bash
uv run --with pyyaml python run_evals.py trigger-prepare
#  ... a judge panel classifies each phrase (descriptions only) into _work/triggering/verdicts.json ...
uv run --with pyyaml python run_evals.py trigger-grade
```

The menu presents the real `opik-evaluate` description alongside the skills whose
phrasing overlaps it most — `opik-test` (one case), `opik-compare` (before/after on an
existing suite), `opik-online-eval` (live scoring). Now that the evaluation half of the
set has four skills, this discrimination is the thing most likely to regress when any
one description changes.

## Functional contract (pinned, not yet automated)

`result.json = {status, shape, cases, scoring, experiment, scores, worst, next_step}` —
`evaluated` must carry `experiment.url` and non-empty `scores`; every `judge` in
`scoring` names one `failure_mode`; the repo is unchanged (the runner lives outside it).

## Metric

`selection_accuracy` (target 1.0).

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack.
