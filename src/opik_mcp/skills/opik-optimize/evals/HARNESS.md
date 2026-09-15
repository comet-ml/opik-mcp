# `/opik-optimize` evals

Triggering suite for the `opik-optimize` skill, plus the pinned functional contract.
The functional seeder (a weak library prompt + a dataset with expected outputs) and
grader are the next step.

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
uv run --with pyyaml python run_evals.py trigger-grade
```

Decoys: `opik-evaluate` (measures, changes nothing) and `opik` (how to *version* a
prompt) — "make my prompt better" sits between the two.

## Functional contract (pinned, not yet automated)

`result.json = {status, prompt, dataset, metric, optimizer, scores, cost, run_url, next_step}` —
`improved` requires `scores.validation > scores.initial` and a `run_url`; `no_improvement`
still carries the `run_url` and `cost`; the budget (`n_samples`, `max_trials`) is set; a
library prompt is saved as a **new version**; the repo is unchanged. The seeder's prompt
must be weak enough that `MetaPromptOptimizer` at `n_samples=50, max_trials=10` reliably
beats it on held-out data, or the case will flake.

## Metric

`selection_accuracy` (target 1.0).

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack.
