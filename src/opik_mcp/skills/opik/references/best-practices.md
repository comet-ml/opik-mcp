# Best Practices: Build, Evaluate, and Monitor AI Agents

Best practices for the agent lifecycle beyond basic tracing — architecture patterns, evaluation, metrics, and production monitoring. This is reference material for the `opik` skill. For SDK details (tracing, integrations, span types) see the other `opik` references; to actually build and run an evaluation, use the `opik-evaluate` skill.

## The Agent Lifecycle

1. **Instrument** — Add Opik tracing to make your agent's behavior visible (see `opik` skill)
2. **Evaluate** — Measure performance with datasets, metrics, and experiments
3. **Monitor** — Track quality, cost, and reliability in production
4. **Optimize** — Improve based on data from evaluation and production traces

## Agent Architecture Patterns

Trace every component of your agent with appropriate span types:

```python
import opik

@opik.track(name="research_agent")
def agent(query: str) -> str:
    plan = plan_action(query)        # general span
    results = execute_tool(plan)     # tool span
    return generate_response(results) # llm span

@opik.track(type="tool")
def execute_tool(action: dict) -> str:
    return search_web(action["query"])

@opik.track(type="llm")
def generate_response(context: str) -> str:
    return llm_call(context)
```

### What to Trace

| Component | Span Type | Key Data |
|-----------|-----------|----------|
| Planning | `general` | Reasoning steps, decisions |
| Tool calls | `tool` | Tool name, parameters, results |
| LLM calls | `llm` | Prompt, response, tokens |
| Retrieval | `tool` | Query, documents |
| Validation | `guardrail` | Check results, pass/fail |

## Evaluation

### Test Suites (Recommended)

Test Suites with `run_tests()` / `runTests()` are the recommended way to evaluate agents. Assertions are plain strings checked by an LLM judge, with execution policies for multi-run reliability:

**Python:**

```python
import opik

client = opik.Opik()
suite = client.get_or_create_test_suite(
    name="my-agent-suite",
    global_assertions=[
        "Response is factually accurate and not hallucinated",
        "Response is professional in tone",
    ],
    global_execution_policy={"runs_per_item": 3, "pass_threshold": 2},
)

results = opik.run_tests(
    test_suite=suite,
    task=lambda item: {"output": agent(item["input"])},
)
assert results.all_items_passed  # CI gate
```

**TypeScript:**

```typescript
import { Opik, runTests } from "opik";

const client = new Opik();
const suite = await client.getOrCreateTestSuite({
  name: "my-agent-suite",
  globalAssertions: [
    "Response is factually accurate and not hallucinated",
    "Response is professional in tone",
  ],
  globalExecutionPolicy: { runsPerItem: 3, passThreshold: 2 },
});

const results = await runTests({
  testSuite: suite,
  task: async (item) => ({
    input: item.input,
    output: await agent(item.input as string),
  }),
});
if (!results.allItemsPassed) process.exit(1); // CI gate
```

See `evaluation-test-suites.md` for full API (items, versioning, execution policies, CI integration).

### Legacy Dataset Evaluation

For custom metric-based evaluation (existing workflows or explicit user request), use `evaluate()` with datasets:

```python
from opik.evaluation import evaluate
from opik.evaluation.metrics import AnswerRelevance, Hallucination, AgentTaskCompletionJudge

results = evaluate(
    experiment_name="agent-v2",
    dataset=dataset,
    task=lambda item: {"output": agent(item["input"])},
    scoring_metrics=[
        AnswerRelevance(),
        Hallucination(),
        AgentTaskCompletionJudge(),
    ]
)
```

See `evaluation-datasets.md` for full API (datasets, versioning, 60+ metrics, annotation queues).

## Production Monitoring

- **Dashboards** — Visualize quality, cost, latency, and error trends
- **Online evaluation** — Automatically score production traces with LLM-as-Judge
- **Alerts** — Get notified when metrics deviate (quality drops, cost spikes, error rates)
- **Guardrails** — PII detection, topic validation, custom safety checks
- **Ollie** — AI-powered root cause analysis for failed traces (Opik's AI assistant)

## Common Anti-Patterns

| Category | Anti-Pattern |
|----------|-------------|
| Reliability | Unbounded loops, retry storms, silent failures |
| Security | Prompt injection, privilege escalation, data leakage |
| Observability | Late tracing (missing input), orphaned spans |
| Tools | Tool loops, hallucinated tools, parameter errors |

## Detailed References

| Topic | Reference File |
|-------|----------------|
| Agent architecture, reliability, security patterns | `agent-patterns.md` |
| **Test Suites, `run_tests()`, assertions, CI gating (recommended)** | `evaluation-test-suites.md` |
| Legacy datasets, `evaluate()`, 60+ metrics, annotation queues | `evaluation-datasets.md` |
| Production dashboards, alerts, guardrails, cost tracking | `production.md` |
