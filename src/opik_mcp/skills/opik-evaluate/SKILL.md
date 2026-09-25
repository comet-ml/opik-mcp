---
name: opik-evaluate
description: Build an LLM evaluation and run it against the app, returning an Opik experiment with scores and its link. Picks a test suite with judge assertions or a dataset with metrics, sources cases from traces or synthetic data, scores heuristics-first then one-failure-mode judges, runs client-side via the SDK or server-side for prompt-only targets, and reads the scores back. Covers RAG evaluation, error analysis, writing and validating LLM judges against human labels, and auditing an existing eval pipeline. Use for "evaluate my agent", "measure quality", "build an eval", "write an LLM judge for hallucinations", "audit our evaluation pipeline", "how good is my RAG", "set up evals for this". Not for before/after on an existing suite (use compare), one regression case (use test), scoring production traffic (use online-eval), or the ship/hold decision (use verify).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project with Opik configured. Install the `opik` skill alongside this one — it holds the shared test-suite, dataset, and metric references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
  - Write
metadata:
  last_updated: "2026-09-15"
  source_commit: "2.0.0"
  argument-hint: "[optional: what to evaluate, or a dataset/suite name]"
---

# Evaluate — Build an Evaluation and Run It

**Definition of done:** an **experiment in Opik with scores**, its **link**, and a **score summary** the user can act on — produced by running the user's app (or prompt) over a set of cases with scoring that targets real failure modes. If the evaluation can't be built or run, stop at the **first** genuine blocker and return **exactly one** next step. A dataset with no run, a judge prompt with no scores, or a plan in prose is not success.

Operate: **ground the cases in what actually goes wrong, score with code before judges, judge one failure mode at a time, run once end to end, read the scores back from Opik — and change no application code.** The only file this skill writes is a runner outside the repo.

## Inputs

The entry point is `/opik-evaluate` (evaluate the app in this repo), `/opik-evaluate <what>` ("the RAG answers", "the refund flow"), or `/opik-evaluate <dataset-or-suite>` (run against existing cases). Infer the rest; treat these as **optional overrides**:

- cases source (default: existing suite/dataset → recent traces → synthetic) · scoring (default: heuristics where an expected output exists, else one judge per failure mode) · approach (default: **test suite** for agents; **dataset + `evaluate()`** when you need built-in metrics or exact matching) · judge model · sample size (default: 20–50 items).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Resolve the target
Confirm Opik is reachable (`~/.opik.config` or `OPIK_API_KEY`; otherwise → **Blocker**). Find the entrypoint to evaluate (the function a trace's root span names, or the one the user points at). Check what already exists — `client.get_test_suites(project_name=…)`, `client.get_datasets()` — and reuse before creating.

**Have an eval already?** Audit it first (`references/eval-audit.md`): unvalidated judges, no error analysis, vanity metrics. Fix the worst gap, then run.

### 2. Ground it in failures (error analysis)
Read real traces before writing any scorer — `client.search_traces(project_name=…, max_results=100)` (errors, low scores, long durations first). Categorize what goes wrong and how often (`references/error-analysis.md`). No traces yet → generate cases (`references/generate-synthetic-data.md`) and say so in the report.

### 3. Choose the shape
| Situation | Use |
|---|---|
| Agent / chatbot, expectations are behaviors ("mentions Paris", "declines legal advice") | **Test suite** — items + string assertions checked by a judge; `opik.run_tests()` |
| Exact expected outputs, or built-in metrics (Hallucination, AnswerRelevance, RAG `ContextPrecision`/`ContextRecall`) | **Dataset + `evaluate()`** with `scoring_metrics` |
| The thing under test is a prompt version, not code | **Server-side**: `client.rest_client.experiments.execute_experiment(...)` — no runner |

Prefer the test suite for agents; it is what `/opik-test` and `/opik-compare` operate on.

### 4. Build the cases
The test-suite calls: `references/sdk-snippets.md` (**Build the cases**).
For the dataset path: `client.get_or_create_dataset(name, project_name)` then `dataset.insert([{"input": …, "expected_output": …}])`. Store inputs verbatim; keep `source_trace_id` so cases trace back to production.

### 5. Define the scoring
1. **Code first.** `Equals`, `Contains`, `RegexMatch`, `IsJson`, `JsonSchemaMatch`, `LevenshteinRatio` from `opik.evaluation.metrics` whenever the check is mechanical.
2. **Then one judge per failure mode**, binary pass/fail, from step 2's categories (`references/write-judge-prompt.md`). As a suite assertion, or as a `GEval` / custom `BaseMetric` on the dataset path.
3. **RAG:** score retrieval and generation separately (`references/evaluate-rag.md`).
4. Before trusting a judge on anything that matters, calibrate it against a few human labels (`references/validate-evaluator.md`) — TPR/TNR, not accuracy.

### 6. Run it
Write the task adapter as a temp file **outside the repo** (needs the app's provider credentials — absent → **Blocker**). Never run a production entrypoint that writes, sends, or spends.
The `run_tests` and `evaluate()` calls: `references/sdk-snippets.md` (**Run it**).
`project_name` matters: datasets, suites, prompts, and experiments are project-scoped, and it must match the tracing project if the app uses `@track`. Set it when **creating** the dataset or suite — `evaluate()` inherits the dataset's project, and its own `project_name` kwarg is deprecated (the SDK warns and ignores it).

**Judge credential guard:** if the LLM judge (suite assertions, or an LLM metric) has no provider key, `run_tests`/`evaluate` do **not** raise — every item scores 0 with `scoring_failed=True` and a "Missing credentials" reason, and the experiment is still created. Check for that before reporting; it is a **Blocker** ("set the judge's provider key and rerun"), not a result.

### 7. Read the scores back
The SDK read and the aggregates: `references/sdk-snippets.md` (**Read the scores back**).
On the dataset path `res.aggregate_evaluation_scores().aggregated_scores` gives per-metric statistics directly. Name the worst items and the failure mode each hit — that is the actionable part. (`get_experiment_by_name` is deprecated; use `get_experiments_by_name` / `get_experiment_by_id`.)

### 8. Report
Experiment link, the score table, the three worst items with their reasons, what the cases were grounded in (traces vs synthetic), and one next step (see **Output**). This run is the **baseline** `/opik-compare` will compare against.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-evaluate`."
- "Which function should I evaluate? Point me at the entrypoint (e.g. `answer(question)`)."
- "The runner needs a provider credential — set `OPENAI_API_KEY` (or the relevant key) and rerun."
- "No traces and no cases yet — give me 5–10 example inputs (with expected behavior if known), or say `synthetic` and I'll generate them."

## Output

**User-facing:** a short human message — the experiment link, the score table, the worst items with reasons, the case source, and the single next step. Not a raw dump of every item.

**Underneath** (for composition / evals), one shape, with its invariants: `references/output-shape.md`.

## Examples

Worked runs (agent from traces, RAG dataset path, prompt only, blocked): `references/examples.md`.

## Key principles
- **Error analysis before evaluators.** Never write a scorer without reading traces first.
- **Code checks before LLM judges.** Heuristic metrics wherever the check is mechanical.
- **Binary pass/fail, one failure mode per judge.** Holistic judges give unactionable verdicts.
- **Validate judges against human labels** before they gate anything (TPR/TNR).
- **Always pass `project_name` where the object is created.** To `get_or_create_dataset`, `get_or_create_test_suite`, `create_prompt`. `evaluate()` and `run_tests()` inherit it from the dataset/suite (the `evaluate(project_name=…)` kwarg is deprecated).
- **Read results from Opik, not from stdout** — so `/opik-compare` reads the same numbers later.

## Anti-patterns
Building a judge before reading a single trace; a "quality 1–10" judge; scoring with a judge what `Equals` could check; a dataset with no run; reporting an aggregate without naming the worst cases; writing the runner into the repo; editing app code to make the eval pass; skipping `project_name`; deprecated `get_experiment_by_name`.

## References

Methodology, in this skill's own references: `references/eval-audit.md` (audit an existing pipeline), `references/error-analysis.md` (failure categorization from traces), `references/generate-synthetic-data.md` (dimension-based inputs), `references/write-judge-prompt.md` (binary judges), `references/validate-evaluator.md` (TPR/TNR calibration), `references/evaluate-rag.md` (retrieval vs generation).

SDK detail lives in the `opik` skill, installed beside this one — paths relative to this file: `../opik/references/evaluation-test-suites.md` (suites, `run_tests`, results), `../opik/references/evaluation-datasets.md` (`evaluate()`, 60+ metrics, OQL, datasets from traces), `../opik/references/production.md` (`search_traces`). If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
