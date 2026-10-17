# `/opik-instrument` evals

Test cases, automation, and success metrics for the `opik-instrument` skill —
mirrors the `opik-diagnose/evals` layout. Where diagnose seeds traces and grades
a shortlist, instrument stages an **app**, has the skill instrument + run + verify
it, and grades whether a **real, complete trace** was confirmed — not just that
code was edited or that "a trace arrived".

## Layout

```
evals/
  cases.yaml            # triggering + functional (clean, missing_flush)
  fixtures/
    clean/              # uninstrumented app; correct instrumentation -> 3-span trace
    missing_flush/      # already instrumented but no flush -> no complete trace (adversarial)
  grader.py             # deterministic: result.json vs expected.json (+ optional online integrity)
  metrics.py            # aggregate -> metrics
  run_evals.py          # orchestrator: prepare stages fixtures, grade scores result.json
  _work/                # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). Running the skill on a fixture needs
an **LLM provider key** (e.g. `OPENAI_API_KEY`) and **Opik configured**
(`~/.opik.config` or `OPIK_API_KEY`), because the skill runs the app and confirms
a real trace. Grading is offline; the optional integrity re-read uses Opik only
when a `trace_id` is present and Opik is reachable.

```bash
uv run --with pyyaml python run_evals.py prepare     # stage fixture apps under _work/
#  ... run /opik-instrument in each _work/<case> dir (see PROMPT.txt). The skill
#      writes result.json in that dir (contract below).
uv run --with pyyaml python run_evals.py grade       # score result.json vs expected.json
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare
#  ... a judge panel classifies each phrase (descriptions only) into verdicts.json ...
uv run --with pyyaml python run_evals.py trigger-grade
```
The menu presents the real `opik-instrument` description alongside decoys
(`opik-diagnose`, `opik-explain`, `opik-evaluate`, `opik`, `code-review`), so
"add tracing" must select instrument and not the neighbours.

## The `result.json` contract

The skill writes this into the workdir after running:

```json
{
  "status": "verified | blocked | already_verified | unsupported",
  "trace_id": "0f1e...", "trace_url": "https://.../traces/...",
  "changes": ["added opik to pyproject", "wrapped OpenAI client with track_openai", "..."],
  "next_step": "add opik.flush_tracker() before exit, then re-run",
  "coverage": {
    "expected_sites": 3,
    "spans_found": 3,
    "spans": [
      {"name": "run", "type": "general"},
      {"name": "generate", "type": "llm"},
      {"name": "retrieve", "type": "tool"}
    ]
  }
}
```

`coverage` is what makes this an eval of the *verify-coverage* ability (OPIK-8185):
the skill must report which spans it actually confirmed, not just a boolean.

## What each case proves

- **clean** — correct instrumentation must land AND `verified` a complete trace,
  reporting all three span types, every span well-formed, with code changed.
- **missing_flush** (adversarial) — already instrumented but no flush, so no
  complete trace lands. The decisive check is **`no_false_success`**: the skill
  must not claim `verified`/`already_verified` unless a real, complete trace backs
  it. It passes whether it fixes the flush and verifies, or returns `blocked` with
  a flush next-step. A skill that trusts the decorators and reports success **fails**.

## Metrics

`selection_accuracy`, `verify_correctness`, `no_false_success_rate` (target 1.0),
`coverage_reported_rate`, `type_coverage`, `well_formed_rate`, `instrumented_rate`,
`integrity_rate`, `schema_compliance`. See `metrics.py`.

## TODO — a `partial_trace` fixture

A stronger adversarial case is a trace that **arrives but is incomplete** at the
span level (the batching race: fast spans dropped or returned unnamed), where the
correct outcome is `blocked` with `spans_found < expected_sites`. Inducing that
deterministically needs a fixture that reliably drops a span at runtime (rather
than the always-empty missing-flush case). Tracked for a follow-up; the grader
already supports it via `coverage.spans_found` vs `expected_sites`.
