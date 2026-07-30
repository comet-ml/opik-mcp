---
last_updated: "2026-04-17"
source_commit: "2.0.0"
---

# Legacy Dataset Evaluation & Metrics Guide

> **This is the LEGACY evaluation path.** For new projects, use **Test Suites** with `run_tests()` instead (see `references/evaluation-test-suites.md`). Only use this Dataset + `evaluate()` approach if the user is already using it or explicitly requests custom metric-based evaluation.

> **Project scoping:** Datasets, prompts, and experiments are project-scoped. Pass `project_name` to `get_or_create_dataset`, `create_dataset`, and `evaluate` so entities land in the right project. If the user also uses `@track` tracing, the `project_name` in `opik.configure(project_name=...)` must match the `project_name` passed to these APIs — otherwise traces and entities end up in different projects.

## When to Use This (vs Test Suites)

| Use Datasets + `evaluate()` when... | Use Test Suites + `run_tests()` when... |
|---|---|
| You need custom scoring metrics (heuristic or LLM-as-judge) | You want simple string assertions checked by an LLM judge |
| You need fine-grained per-metric scores | You need pass/fail with execution policies |
| You're migrating from an existing dataset-based workflow | Starting fresh or building CI gates |
| You need RAG-specific metrics (ContextPrecision, etc.) | You want built-in multi-run reliability testing |

## Core Concepts

### Datasets

A **dataset** is a collection of test cases for evaluating your LLM application.

Each dataset item contains:
- **Input**: The query/prompt to send to your application
- **Expected output** (optional): The ground truth or reference answer
- **Custom fields**: Any additional context needed for evaluation

### Experiments

An **experiment** is a single evaluation run that:
1. Processes each dataset item through your LLM application
2. Computes the actual output
3. Scores the output using one or more metrics
4. Logs results for analysis

## Creating Datasets

### Via Python SDK

```python
from opik import Opik

client = Opik()
dataset = client.get_or_create_dataset(name="my-evaluation-dataset", project_name="my-project")

# Insert items
dataset.insert([
    {
        "input": "What is the capital of France?",
        "expected_output": "Paris"
    },
    {
        "input": "Explain quantum computing in simple terms",
        "expected_output": "Quantum computing uses quantum mechanics..."
    }
])
```

### From Production Traces

In the Opik UI:
1. Go to your project's traces
2. Select traces you want to use
3. Click "Add to dataset" in Actions dropdown

### From CSV/JSON

Upload files directly through the Opik UI or API.

### From Pandas DataFrame

```python
import pandas as pd
from opik import Opik

client = Opik()
dataset = client.get_or_create_dataset(name="from-pandas", project_name="my-project")

df = pd.DataFrame({
    "input": ["What is ML?", "Explain AI"],
    "expected_output": ["Machine learning is...", "AI is..."]
})

dataset.insert_from_pandas(df)
```

### From JSONL Files

```python
dataset.read_jsonl_from_file("path/to/data.jsonl")
```

## Dataset Versioning

Opik supports immutable dataset versions for reproducible evaluations.

### Accessing Versions

Opik automatically versions datasets when items are inserted or modified.

```python
from opik import Opik

client = Opik()
dataset = client.get_dataset(name="my-dataset")

# Get current version name
current = dataset.get_current_version_name()
print(f"Current version: {current}")

# Get detailed version info
info = dataset.get_version_info()
print(f"Version: {info.version_name}, items: {info.items_total}")

# Get a read-only view of a specific version for reproducible evaluation
version_view = dataset.get_version_view("v1")
print(f"Version {version_view.version_name}: {version_view.items_total} items")
```

### Evaluating a Specific Version

```python
# Pin evaluation to a specific dataset version
version_view = dataset.get_version_view("v1")
results = evaluate(
    experiment_name="test-v1",
    dataset=version_view,  # Pass the version view directly
    task=evaluation_task,
    scoring_metrics=[AnswerRelevance()],
    project_name="my-project",
)
```

### Version History

In the UI:
1. Go to dataset details
2. Click "Versions" tab
3. View version history with timestamps
4. Compare versions side-by-side

## AI Expansion (Synthetic Data)

Generate synthetic test data to expand your datasets.

### Using AI Expansion

In the Opik UI:
1. Go to your dataset
2. Click "AI Expansion"
3. Select seed examples (optional)
4. Configure expansion parameters:
   - Number of new items
   - Diversity settings
   - Topic constraints
5. Review and approve generated items

AI Expansion is a UI-only feature and is not available via the Python SDK.

## OQL: Opik Query Language

Filter datasets and traces using OQL syntax.

### Basic Syntax

```
field_name operator value
```

### Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `=` | Equals | `status = "success"` |
| `!=` | Not equals | `model != "gpt-3.5"` |
| `>`, `<`, `>=`, `<=` | Comparison | `score > 0.8` |
| `contains` | Substring match | `input contains "error"` |
| `in` | List membership | `tag in ["prod", "staging"]` |
| `exists` | Field exists | `metadata.user_id exists` |

### Combining Filters

```
# AND (implicit)
score > 0.8 model = "gpt-4"

# OR (explicit)
score > 0.9 OR model = "gpt-4"

# Parentheses for grouping
(score > 0.8 AND model = "gpt-4") OR tag = "important"
```

### Examples

```python
# Filter traces
traces = client.search_traces(
    project_name="production",
    filter_string='score > 0.7 AND metadata.user_type = "premium"'
)

# Filter dataset items
items = dataset.get_items(
    filter_string='input contains "error" AND expected_output exists'
)
```

## Annotation Queues

Create review workflows for expert human evaluation.

### Creating a Queue

In the Opik UI:
1. Go to Project > Annotation Queues
2. Click "Create Queue"
3. Configure:
   - Queue name
   - Description and reviewer instructions
   - Feedback definitions (scores/labels to collect)
   - Assignees

### Via Python SDK

Opik provides separate queue types for traces and threads:

```python
from opik import Opik

client = Opik()

# Create annotation queue for traces
queue = client.create_traces_annotation_queue(
    name="quality-review",
    project_name="production",
    description="Review agent responses for quality",
    instructions="Rate accuracy 1-5, flag any hallucinations",
    comments_enabled=True,
    feedback_definition_names=["accuracy", "helpfulness"],
)

# Create annotation queue for threads (multi-turn conversations)
thread_queue = client.create_threads_annotation_queue(
    name="conversation-review",
    project_name="production",
    description="Review multi-turn conversations",
    feedback_definition_names=["conversation_quality"],
)

# Add traces to a queue by searching
traces = client.search_traces(project_name="production", filter_string='score < 0.5')
queue.add_traces(traces=traces)

# Or add individual traces by ID
trace = client.get_trace_content(id="trace-id-1")
queue.add_traces(traces=[trace])

# Add threads to a queue
threads = client.search_threads(project_name="production")
thread_queue.add_threads(threads=threads)
```

### Reviewing Items

1. Go to your annotation queue
2. Items appear based on what was added
3. For each item:
   - View trace details
   - Apply scores and labels
   - Add comments
   - Submit annotation
4. Progress is tracked per reviewer

## Running Evaluations

### Basic Evaluation

```python
from opik import Opik
from opik.evaluation import evaluate
from opik.evaluation.metrics import Equals, AnswerRelevance

client = Opik()
dataset = client.get_dataset(name="my-dataset", project_name="my-project")

# Define the task (how to process each item)
def evaluation_task(dataset_item):
    # Your LLM application logic
    response = my_llm_call(dataset_item["input"])
    return {"output": response}

# Run evaluation
results = evaluate(
    experiment_name="baseline-v1",
    dataset=dataset,
    task=evaluation_task,
    scoring_metrics=[
        Equals(),              # Exact match
        AnswerRelevance()      # LLM-as-Judge
    ],
    project_name="my-project",
)
```

### Evaluation with Context

For RAG applications:

```python
def evaluation_task(dataset_item):
    query = dataset_item["input"]

    # Retrieve context
    context = retrieve_documents(query)

    # Generate response
    response = generate_with_context(query, context)

    return {
        "output": response,
        "context": context  # Pass to metrics
    }

results = evaluate(
    experiment_name="rag-v1",
    dataset=dataset,
    task=evaluation_task,
    scoring_metrics=[
        ContextPrecision(),
        ContextRecall(),
        Hallucination()
    ],
    project_name="my-project",
)
```

## Built-in Metrics (60+)

Opik provides 60+ built-in metrics organized into categories. All are importable from `opik.evaluation.metrics`.

### Heuristic Metrics

Deterministic, rule-based checks that don't require LLM calls:

**Text Similarity:**
- `Equals` - Exact string match
- `Contains` - Substring presence
- `RegexMatch` - Pattern matching
- `LevenshteinRatio` - Edit distance ratio
- `SentenceBLEU` / `CorpusBLEU` - Translation quality (n-gram overlap)
- `ROUGE` - Summarization quality (recall-oriented)
- `GLEU` - Generalized language evaluation understudy
- `ChrF` - Character n-gram F-score
- `METEOR` - Machine translation metric
- `BERTScore` - Semantic similarity using embeddings
- `SpearmanRanking` - Rank correlation

**Validation & Analysis:**
- `IsJson` - Valid JSON check
- `Sentiment` - Sentiment analysis
- `VADERSentiment` - Rule-based sentiment analysis
- `Readability` - Text readability scoring
- `Tone` - Tone detection
- `PromptInjection` - Detects prompt injection attempts
- `LanguageAdherenceMetric` - Checks language consistency

**Statistical:**
- `JSDivergence` / `JSDistance` - Jensen-Shannon divergence/distance
- `KLDivergence` - Kullback-Leibler divergence

### LLM-as-Judge Metrics

Use an LLM to evaluate semantic quality:

**Quality Assessment:**
- `AnswerRelevance` - Does the answer address the question?
- `Hallucination` - Are there unsupported claims?
- `Usefulness` - How useful is the response?
- `Moderation` - Safety and policy violations
- `GEval` - Configurable custom criteria
- `GEvalPreset` - Pre-built evaluation criteria
- `SycEval` - Sycophancy detection
- `StructuredOutputCompliance` - Validates structured output format
- `LLMJuriesJudge` - Multi-judge ensemble evaluation

**RAG-Specific:**
- `ContextPrecision` - Is only relevant context used?
- `ContextRecall` - Is all relevant context used?

### GEval Preset Judges

Pre-built judge metrics for common evaluation scenarios:

- `AgentTaskCompletionJudge` - Did the agent complete its task?
- `AgentToolCorrectnessJudge` - Were tools used correctly?
- `ComplianceRiskJudge` - Compliance risk assessment
- `DemographicBiasJudge` / `GenderBiasJudge` / `PoliticalBiasJudge` / `RegionalBiasJudge` / `ReligiousBiasJudge` - Bias detection
- `DialogueHelpfulnessJudge` - Dialogue quality
- `PromptUncertaintyJudge` - Uncertainty detection
- `QARelevanceJudge` - QA relevance scoring
- `SummarizationCoherenceJudge` / `SummarizationConsistencyJudge` - Summarization quality

### Conversation Metrics

For multi-turn conversation analysis:

- `ConversationalCoherenceMetric` - Flow between turns
- `ConversationDegenerationMetric` - Detects conversation quality decline
- `KnowledgeRetentionMetric` - Tracks knowledge consistency across turns
- `SessionCompletenessQuality` - Overall session effectiveness
- `UserFrustrationMetric` - Detects user frustration signals
- `ConversationThreadMetric` - General thread-level evaluation

**Conversation GEval Wrappers** (apply GEval presets to full conversations):
- `GEvalConversationMetric` - Custom criteria on conversations
- `ConversationDialogueHelpfulnessMetric` - Helpfulness across turns
- `ConversationQARelevanceMetric` - QA relevance across turns
- `ConversationComplianceRiskMetric` / `ConversationSummarizationCoherenceMetric` / `ConversationSummarizationConsistencyMetric` / `ConversationPromptUncertaintyMetric`

### Agent-Specific Metrics

For evaluating agentic behavior:

- `AgentTaskCompletionJudge` - Did the agent complete its task?
- `AgentToolCorrectnessJudge` - Were tools used correctly?
- `TrajectoryAccuracy` - Did the agent follow expected steps?

## Using Metrics

### Simple Scoring

```python
from opik.evaluation.metrics import Hallucination

metric = Hallucination()

result = metric.score(
    input="What is the capital of France?",
    output="The capital of France is Paris. It has the Eiffel Tower.",
    context=["Paris is the capital of France."]
)

print(result.value)   # 0.0 (no hallucination)
print(result.reason)  # Explanation
```

### Custom Model for LLM Metrics

```python
from opik.evaluation.metrics import Hallucination

# Use a different LLM as judge
metric = Hallucination(model="bedrock/anthropic.claude-3-sonnet-20240229-v1:0")
```

### G-Eval: Custom Criteria

```python
from opik.evaluation.metrics import GEval

# Define custom evaluation criteria
metric = GEval(
    name="technical_accuracy",
    criteria="""
    Evaluate the technical accuracy of the response:
    1. Are technical terms used correctly?
    2. Are explanations factually accurate?
    3. Is the complexity appropriate for the audience?
    """,
    model="gpt-4"
)
```

## Custom Metrics

Create your own metrics:

```python
from opik.evaluation.metrics import BaseMetric, ScoreResult

class ResponseLengthMetric(BaseMetric):
    def __init__(self, min_length: int = 50, max_length: int = 500):
        self.name = "response_length"
        self.min_length = min_length
        self.max_length = max_length

    def score(self, output: str, **kwargs) -> ScoreResult:
        length = len(output)

        if self.min_length <= length <= self.max_length:
            return ScoreResult(
                name=self.name,
                value=1.0,
                reason=f"Length {length} is within acceptable range"
            )
        else:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Length {length} outside range [{self.min_length}, {self.max_length}]"
            )
```

## Experiment-Level Metrics

Compute aggregate metrics across all test results:

```python
def compute_experiment_scores(test_results):
    scores = [r.scores.get("accuracy", 0) for r in test_results]
    return {
        "mean_accuracy": sum(scores) / len(scores),
        "min_accuracy": min(scores),
        "pass_rate": sum(1 for s in scores if s > 0.8) / len(scores)
    }

results = evaluate(
    experiment_name="with-aggregates",
    dataset=dataset,
    task=evaluation_task,
    scoring_metrics=[AnswerRelevance()],
    experiment_scoring_functions=[compute_experiment_scores],
    project_name="my-project",
)
```

## Comparing Experiments

In the Opik UI:
1. Go to your dataset's experiments
2. Select experiments to compare
3. View side-by-side metrics
4. Analyze per-item differences

## Evaluation Best Practices

### Dataset Design

1. **Representative samples**: Cover edge cases and typical usage
2. **Clear expected outputs**: When possible, include ground truth
3. **Version your datasets**: Track changes over time
4. **Balance coverage**: Include examples across all use cases

### Metric Selection

1. **Start simple**: Begin with heuristic metrics
2. **Add LLM judges**: For semantic quality
3. **Custom metrics**: For domain-specific requirements
4. **Multiple metrics**: Capture different quality dimensions

### Experiment Workflow

1. **Baseline first**: Establish current performance
2. **Change one variable**: Isolate impact of changes
3. **Document config**: Track model, prompt, parameters
4. **Iterate systematically**: Use data to guide improvements

## Online Evaluation

Run metrics automatically on production traces:

### Setting Up Rules

In the Opik UI:
1. Go to Project Settings > Evaluation Rules
2. Create a new rule
3. Select the metric
4. Configure sampling (all traces or percentage)
5. Activate the rule

### Supported Online Metrics

- Answer Relevance
- Hallucination
- Moderation
- Custom LLM-as-Judge rules

## TypeScript Evaluation

```typescript
import { Opik, evaluate, Hallucination } from "opik";

const client = new Opik();
const dataset = await client.getDataset("my-dataset", "my-project");

const results = await evaluate({
  experimentName: "ts-evaluation",
  dataset,
  projectName: "my-project",
  task: async (item) => {
    const response = await myLLM(item.input);
    return { output: response };
  },
  scoringMetrics: [new Hallucination({ model: "gpt-4o" })]
});
```

## Troubleshooting

### Metrics returning unexpected scores

- Check input/output field names match metric expectations
- Verify context is passed for RAG metrics
- Review the `reason` field for explanation

### Slow evaluations

- Use batch APIs when possible
- Consider sampling large datasets
- Choose faster models for LLM-as-Judge metrics

### Inconsistent LLM-as-Judge scores

- Set `temperature=0` for deterministic results
- Use `seed` parameter when available
- Run multiple trials and average
