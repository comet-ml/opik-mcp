# `/opik-online-eval` evals

Test cases, automation, and success metrics for the `opik-online-eval` skill.

## Layout

```
evals/
  cases.yaml            # triggering + functional (judge_live) + edge (already_exists)
  fixtures/traffic/     # seed.py: fresh project + six traces (+ a pre-created rule for the edge case)
                        # traffic.py: three more traced requests, for the skill's verify step
  grader.py             # deterministic: status, cost cap, sampling, field-path variables, verification trace, dedupe, read-only
  metrics.py            # aggregate -> the metrics below
  run_evals.py          # prepare (seed) / grade (offline) / verify (network) / trigger-*
  _work/                # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). `prepare` needs **Opik configured** and
network. Rules run on the workspace's built-in free provider (`opik-free-model`), so
**no LLM key is needed** — for the seeder or for the skill. Grading is offline.

```bash
uv run --with pyyaml python run_evals.py prepare
#  ... run /opik-online-eval with cwd = _work/judge_live/ (then _work/already_exists/), prompt in PROMPT.txt.
#      Each should write result.json = {status, rule, score_name, variables, verification, source, next_step}.
uv run --with pyyaml python run_evals.py grade
uv run --with pyyaml python run_evals.py verify     # exactly one rule of that name in the project
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare
uv run --with pyyaml python run_evals.py trigger-grade
```
Decoys: `opik-evaluate` (offline experiment) and `opik-diagnose` (reads existing
scores, creates no rule) — the two the phrase "score my traces" can be mistaken for.

## What the cases check

The seeder plants six `answer` traces (input `{"question"}`, output `{"output"}`) in a
fresh project; the workdir's `traffic.py` sends three more on demand.

- **judge_live** — one rule created with `max_cost_usd` and `sampling_rate`, variables as
  field paths (no `{{`), and a `verification.trace_id` from a freshly scored trace → `live`.
- **already_exists** — the seeder pre-created `refund_window_correct`; the skill must find
  it (`rule.id` equals the planted id), create nothing, return `exists`. `verify` confirms
  the project still holds exactly one rule of that name.

Both cases require an unchanged workdir (the skill writes to Opik, not the repo).

## Metrics

`selection_accuracy` · `live_rate` · `guardrail_rate` · `mapping_rate` · `verified_rate` ·
`dedupe_rate` · `read_only_rate` (target 1.0) · `schema_compliance`.

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack.
