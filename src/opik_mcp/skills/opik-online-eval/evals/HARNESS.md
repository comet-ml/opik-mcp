# `/opik-online-eval` evals

Triggering suite for the `opik-online-eval` skill, plus the pinned functional contract.
The functional seeder (a fresh project receiving traces) and grader are the next step.

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

Decoys: `opik-evaluate` (offline experiment) and `opik-diagnose` (reads existing
scores, creates no rule) — the two the phrase "score my traces" can be mistaken for.

## Functional contract (pinned, not yet automated)

`result.json = {status, rule, score_name, variables, verification, source, next_step}` —
`live` carries `verification.trace_id`; every created rule has `max_cost_usd` and
`sampling_rate`; `variables` are field paths (no `{{`); `exists` created nothing; the
repo is unchanged. The seeder should also pre-create a same-named rule for the
`already_exists` case so the dedupe path is exercised.

## Metric

`selection_accuracy` (target 1.0).

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack.
