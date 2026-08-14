---
name: evaluate
description: >
  Build an LLM evaluation and run it against your app, returning an experiment
  with scores. Covers datasets, LLM judges, RAG evaluation, synthetic data,
  error analysis, and validating evaluators against human labels. Use when the
  user wants to measure or improve AI product quality, or asks about evals,
  judges, or evaluation metrics.
metadata:
  last_updated: "2026-07-30"
  source_commit: "2.0.0"
---

# LLM Evaluation

Help users build, audit, and improve evaluation systems for LLM pipelines.

## Where to Start

**Have an existing eval pipeline?** Start with an eval audit to surface problems: missing error analysis, unvalidated judges, vanity metrics. See the `eval-audit` reference.

**Starting from scratch?** Begin with error analysis on real traces. If no production data exists, generate synthetic data first. See the `error-analysis` and `generate-synthetic-data` references.

## Test Suites

Test suites are the primary way to test agents in Opik. They combine test items with string assertions checked by an LLM judge, plus execution policies for multi-run reliability testing. Available in both Python and TypeScript SDKs.

**Python:**

```python
import opik

client = opik.Opik()
suite = client.get_or_create_test_suite(
    name="my-agent-suite",
    global_assertions=["Response is factually accurate", "Response is professional"],
    global_execution_policy={"runs_per_item": 3, "pass_threshold": 2},
    project_name="my-project",
)

suite.insert([
    {"data": {"input": "What is the capital of France?"}, "assertions": ["Mentions Paris"]},
])

results = opik.run_tests(
    test_suite=suite,
    task=lambda item: {"output": my_agent(item["input"])},
    model="gpt-4o",
)
assert results.all_items_passed
```

**TypeScript:**

```typescript
import { Opik, runTests } from "opik";

const client = new Opik();
const suite = await client.getOrCreateTestSuite({
  name: "my-agent-suite",
  globalAssertions: ["Response is factually accurate", "Response is professional"],
  globalExecutionPolicy: { runsPerItem: 3, passThreshold: 2 },
  projectName: "my-project",
});

await suite.insert([
  { data: { input: "What is the capital of France?" }, assertions: ["Mentions Paris"] },
]);

const results = await runTests({
  testSuite: suite,
  task: async (item) => ({ input: item.input, output: await myAgent(item.input as string) }),
  model: "gpt-4o",
});
if (!results.allItemsPassed) process.exit(1);
```

Key concepts: `global_assertions` / `globalAssertions` apply to every item, `assertions` per-item for high-stakes cases, `execution_policy` / `executionPolicy` controls multi-run reliability (`runs_per_item` / `runsPerItem`, `pass_threshold` / `passThreshold`). Test suites appear under "Test Suites" in the UI sidebar.

## Workflow

The typical evaluation workflow follows this sequence:

1. **Error Analysis** — Read ~100 traces, categorize failures, compute failure rates. This grounds everything in what actually goes wrong.
2. **Generate Synthetic Data** — When real traces are sparse, create diverse test inputs using dimension-based tuple generation.
3. **Build Test Suite** — Create a test suite with string assertions targeting the failure modes found in step 1. Use `opik.run_tests()` for automated regression testing.
4. **Write Judge Prompt** — For failures requiring interpretation (not checkable by code), design a binary Pass/Fail LLM-as-Judge evaluator targeting one specific failure mode.
5. **Validate Evaluator** — Calibrate the judge against human labels using train/dev/test splits. Measure TPR/TNR, iterate until aligned, apply bias correction.
6. **Evaluate RAG** — For RAG pipelines specifically: separate retrieval and generation evaluation using Opik's ContextRecall, ContextPrecision, and Faithfulness metrics.

## Key Principles

- **Error analysis before evaluators.** Never build evaluators without first reading traces and identifying actual failure modes.
- **Test suites for regression.** Once you identify failure modes, encode them as test suite assertions. Run `opik.run_tests()` on every change.
- **Binary Pass/Fail only.** No Likert scales, no letter grades. Binary forces clear decision boundaries.
- **Code checks before LLM judges.** Use Opik's heuristic metrics (`Equals`, `Contains`, `RegexMatch`, `IsJson`, `JsonSchemaMatch`) before reaching for LLM judges.
- **Validate judges against human labels.** An unvalidated judge may consistently miss failures. Measure TPR/TNR.
- **One failure mode per judge.** Holistic judges produce unactionable verdicts.
- **Always pass `project_name`.** Datasets, prompts, and experiments are project-scoped. Pass `project_name` to `get_or_create_dataset`, `create_prompt`, `opik.Prompt(...)`, `get_or_create_test_suite`, and `evaluate`. If the user also uses `@track` tracing, the `project_name` configured for tracing must match.

## Available References

- `eval-audit` — Audit an existing eval pipeline across six diagnostic areas
- `error-analysis` — Systematic failure categorization from trace review
- `generate-synthetic-data` — Dimension-based synthetic test input generation
- `write-judge-prompt` — Design binary Pass/Fail LLM-as-Judge evaluators
- `validate-evaluator` — Calibrate judges with TPR/TNR and bias correction
- `evaluate-rag` — RAG-specific evaluation using Opik's ContextRecall, ContextPrecision, and Faithfulness metrics
