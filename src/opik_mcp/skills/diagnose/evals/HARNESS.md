# `/diagnose` evals

Test cases, automation, and success metrics for the `diagnose` skill —
structured on Anthropic's *Testing and iteration* guidance and OPIK-7648.

## Layout

```
evals/
  cases.yaml        # triggering + functional (triage)
  fixtures/seed/    # seeder: plants error/slow/low-score/normal traces in a fresh project
  grader.py         # deterministic: shortlist vs planted ids + signals + read-only, no network
  metrics.py        # aggregate -> the OPIK-7648 metrics
  run_evals.py      # orchestrator: prepare seeds a project, grade scores result.json
  _work/            # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). `prepare` also needs **Opik configured**
(`~/.opik.config` or `OPIK_API_KEY`) and network, because it seeds real traces.
Grading is offline.

```bash
uv run --with pyyaml python run_evals.py prepare     # seed a fresh project with known traces
#  ... run the /diagnose skill on the project named in _work/triage/PROMPT.txt.
#      It should write result.json = {status, scope, shortlist, source, next_step}.
uv run --with pyyaml python run_evals.py grade       # score shortlist vs planted ids + metrics
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare
#  ... a judge panel classifies each phrase (descriptions only) into verdicts.json ...
uv run --with pyyaml python run_evals.py trigger-grade
```
The menu presents the real `diagnose` description alongside decoys (`explain`,
`instrument`, `evaluate`, `opik`, `code-review`) so the negatives are a real
discrimination test — especially `explain` (debug ONE trace) vs `diagnose` (discover which).

## What the functional case checks

`triage` seeds a unique project with 5 traces (error, slow, low-score, 2 normal) and
records their ids by role. The grader scores the agent's `result.json` shortlist:

- **recall** — the error / slow / low-score planted ids all appear (`include_roles`).
- **precision** — neither normal id leaks in (`exclude_roles`).
- **signal_coverage** — the shortlist covers `error`, `latency`, `low_score`.
- **source** is `sdk` or `mcp`; **one next step**; **status** is `found`.
- **read_only** — the seeder fixture files are unchanged (the skill modifies no code).

A fresh project name per run keeps precision honest (no accumulation from earlier runs).

## Metrics (OPIK-7648)

`selection_accuracy` · `found_rate` · `recall` · `precision` · `signal_coverage` ·
`read_only_rate` (target 1.0) · `schema_compliance`.

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack. Matches the `/instrument`
(OPIK-7800) and `/explain` harnesses.
