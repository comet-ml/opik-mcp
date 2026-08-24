# `/explain` evals

Test cases, automation, and success metrics for the `explain` skill —
structured on Anthropic's *Testing and iteration* guidance (triggering /
functional / edge) and OPIK-7649.

## Layout

```
evals/
  cases.yaml        # declarative cases (triggering + functional + edge)
  fixtures/         # instrumented apps with a KNOWN bug
    toolbug/          retrieval returns empty -> ungrounded answer
    latency/          one span sleeps 3s -> dominates duration
  grader.py         # deterministic scoring of result.json (keyword + exact), no network
  metrics.py        # aggregate -> the OPIK-7649 metrics
  run_evals.py      # orchestrator: prepare emits real traces, grade scores result.json
  _work/            # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). `prepare` also needs **Opik configured**
(`~/.opik.config` or `OPIK_API_KEY`) and network, because it runs each fixture to
emit a real trace. Grading is offline.

```bash
uv run --with pyyaml python run_evals.py prepare     # stage workdirs; run fixtures to emit real traces
#  ... run the /explain skill on each _work/<case>/ (use the id in trace_id.txt).
#      It should write result.json = {status, root_cause, evidence, next_step, reasoner}.
uv run --with pyyaml python run_evals.py grade       # score + emit metrics + report.md
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare   # writes _work/triggering/judge_input.md
#  ... a judge panel classifies each phrase (descriptions only), majority-vote
#      into _work/triggering/verdicts.json = {phrase: skill} ...
uv run --with pyyaml python run_evals.py trigger-grade
```
The menu presents the real `explain` description alongside decoys (`instrument`,
`evaluate`, `opik`, `scaffold-app`, `code-review`) so the negatives are a real
discrimination test. A phrase "triggers" iff the judge picks `explain`.

## What each case checks

- **functional** — `toolbug` (root cause = the empty-retrieval bug; evidence = the
  `retrieve` span) and `latency` (root cause = the slow `fetch_context` span). Each
  must reach `explained`, name the right culprit (keyword match), cite the anchor
  span, give exactly one next step, use a valid reasoner, and **change no code**.
- **edge** — `not_found` (all-zeros id): must reach `not_found`, give one next step,
  and change nothing.

## Metrics (OPIK-7649)

`selection_accuracy` · `explained_rate` · `root_cause_accuracy` ·
`evidence_grounding_rate` · `single_next_step_rate` · `read_only_rate` (target 1.0) ·
`schema_compliance`.

The grader scores the agent's `result.json` deterministically; the root cause is
matched by keyword (natural-language explanation), everything else is exact. The
`read_only_rate` is enforced by diffing the workdir against the fixture — `/explain`
must never modify app code.

## Note

`evals/` is **development tooling** — `build_skills_pack.py` already excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack. It travels with the skill
in-repo, matching the `/instrument` harness (OPIK-7800).
