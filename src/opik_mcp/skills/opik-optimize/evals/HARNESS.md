# `/opik-optimize` evals

Test cases, automation, and success metrics for the `opik-optimize` skill.

## Layout

```
evals/
  cases.yaml            # triggering + functional (library_prompt)
  fixtures/format/      # seed.py: 50 order-line items with a strict expected format + a weak library prompt
  grader.py             # deterministic: validation gain, budget, split, version discipline, read-only
  metrics.py            # aggregate -> the metrics below
  run_evals.py          # prepare (seed) / grade (offline) / verify (network) / trigger-*
  _work/                # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). `prepare` needs **Opik configured** and
network; the seeder makes no LLM calls. **The skill's own run needs a provider key**
(`OPENAI_API_KEY` or equivalent) — the optimizer calls the task model. Grading is offline.

```bash
uv run --with pyyaml python run_evals.py prepare
#  ... run /opik-optimize with cwd = _work/library_prompt/, prompt in PROMPT.txt.
#      It should write result.json = {status, prompt, dataset, metric, optimizer, scores, cost, run_url, next_step}.
uv run --with pyyaml python run_evals.py grade
uv run --with pyyaml python run_evals.py verify     # a new library version exists iff the result says improved
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare
uv run --with pyyaml python run_evals.py trigger-grade
```
Decoys: `opik-evaluate` (measures, changes nothing) and `opik` (how to *version* a
prompt) — "make my prompt better" sits between the two.

## What the case checks

The seeder plants 50 items like `Order 1042: 3 widgets at 12.50 each plus 4.00 shipping. What do I owe?`
with `expected_output` `TOTAL: 41.50`, and a library prompt that says nothing about the
format. The prompt is the bottleneck by construction, so a real validation gain is
available — with a **graded** metric. Observed: `Equals` scored every candidate 0.0 and the
optimizer had nothing to climb (`no_improvement`, prompt unchanged after 5 trials);
`LevenshteinRatio` on the same fixture went 0.08 → 0.23 and saved v2. The skill text now
says so. The honest fallbacks are still accepted:

- **status** — `improved` or `no_improvement`; never a fake gain (`improved` requires `scores.validation > scores.initial`).
- **validation_reported** — the gain is on `scores.validation`, not training.
- **budget_bounded** — `optimizer.n_samples` and `optimizer.max_trials` set before spending.
- **split_reported** — `dataset.train` / `dataset.validation` with counts, validation ≥ 10 (the optimizer scores trials on it).
- **version_iff_improved** — `prompt.new_version` set exactly when `improved`; `verify` checks the library agrees.
- **cost_reported**, **run_url**, **prompt_matches**, **one_next_step**, **no_modifications**.

## Metrics

`selection_accuracy` · `honest_rate` · `validation_rate` · `budget_rate` · `split_rate` ·
`version_discipline` · `read_only_rate` (target 1.0) · `schema_compliance`.

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack.
