# `/opik-compare` evals

Test cases, automation, and success metrics for the `opik-compare` skill —
structured on Anthropic's *Testing and iteration* guidance, OPIK-7651 and OPIK-8400
("a seeder that plants a known regression on known cases and a grader that checks
whether the agent named those cases").

## Layout

```
evals/
  cases.yaml            # triggering + functional (regress) + edge (first_run)
  fixtures/regress/     # seed.py: suite + BASELINE run; agent.py: the CANDIDATE the skill runs
  grader.py             # deterministic: flips by item id, both ids in the link, no verdict, read-only
  metrics.py            # aggregate -> the OPIK-7651 metrics
  run_evals.py          # prepare (seed) / grade (offline) / trigger-*
  _work/                # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). `prepare` needs **Opik configured**
(`~/.opik.config` or `OPIK_API_KEY`), network, and a judge model for the suite's
assertions — `OPIK_EVAL_JUDGE_MODEL` (default `gpt-4o-mini`) with its provider key.
The skill's own candidate run needs the same key. Grading is offline.

```bash
uv run --with pyyaml python run_evals.py prepare
#  ... run /opik-compare with cwd = _work/regress/ (then _work/first_run/), prompt in PROMPT.txt.
#      Each should write result.json = {status, suite, baseline, candidate, deltas,
#      regressions, fixes, compare_url, next_step}.
uv run --with pyyaml python run_evals.py grade
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare
uv run --with pyyaml python run_evals.py trigger-grade
```
Decoys include `opik-test` (ADD a case) and `opik-evaluate` (a NEW eval) — the two
skills whose phrasing overlaps most with "run the suite and tell me what changed".

## What the functional case checks

`regress` seeds a unique suite with four items and a baseline run whose outcome per
item is known; the workdir's `agent.py` is the candidate, which fixes the refund item
and breaks the delivery item:

| role | baseline | candidate |
|---|---|---|
| `fixed` | FAIL | PASS |
| `regressed` | PASS | FAIL |
| `stable_pass` | PASS | PASS |
| `stable_fail` | FAIL | FAIL |

The grader scores `result.json`:
- **baseline_id** — the skill picked the seeded baseline, not some other run.
- **regression:regressed** / **fix:fixed** — named by `dataset_item_id`.
- **stable:*** — neither stable item leaked into a flip list.
- **deltas** — a per-metric table exists; **compare_url** — carries both experiment ids.
- **no_verdict** — no `verdict` key, no ship/hold language (by design: OPIK-7470 defers it).
- **one_next_step**, **no_modifications** — `agent.py` untouched.

`first_run` seeds the suite with **no** baseline; the skill must run once and return
`baseline_created` rather than inventing a comparison.

## Metrics (OPIK-7651)

`selection_accuracy` · `compared_rate` · `flip_recall` · `flip_precision` · `link_rate` ·
`no_verdict_rate` (target 1.0) · `read_only_rate` (target 1.0) · `schema_compliance`.

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack. Matches the other harnesses.
