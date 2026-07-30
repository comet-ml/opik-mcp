---
last_updated: "2026-04-17"
source_commit: "2.0.0"
---

# Evaluate RAG

## Overview

1. Do error analysis on end-to-end traces first. Determine whether failures come from retrieval, generation, or both.
2. Evaluate retrieval and generation separately using Opik's built-in RAG metrics.
3. Use Opik datasets and experiments to track improvements over time.

## Prerequisites

Complete error analysis on RAG pipeline traces before selecting metrics. Inspect what was retrieved vs. what the model needed. Determine whether the problem is retrieval, generation, or both. Fix retrieval first.

## Core Instructions

### Evaluate Retrieval and Generation Separately

Measure each component independently using Opik's RAG metrics:

- **ContextRecall** — Is all relevant context retrieved? Measures whether the retrieval step found the documents needed to answer the query.
- **ContextPrecision** — Is only relevant context used? Measures whether retrieved documents are actually useful, or if noise is diluting the context.
- **Faithfulness** — Does the output align with the provided context? Detects hallucinations where the model generates claims not supported by retrieved documents.

### Building a Retrieval Evaluation Dataset

You need queries paired with ground-truth relevant document chunks. Store these as Opik datasets.

**Manual curation (highest quality):** Write realistic questions and map each to the exact chunk(s) containing the answer.

**Synthetic QA generation (scalable):** For each document chunk, prompt an LLM to extract a fact and generate a question answerable only from that fact.

Synthetic QA prompt template:

```
Given a chunk of text, extract a specific, self-contained fact from it.
Then write a question that is directly and unambiguously answered
by that fact alone.

Return output in JSON format:
{ "fact": "...", "question": "..." }

Chunk: "{text_chunk}"
```

**Adversarial question generation:** Create harder queries that resemble content in multiple chunks but are only answered by one.

Process:
1. Select target chunk A containing a clear fact.
2. Find similar chunks B, C using embedding search (chunks that share terminology but lack the answer).
3. Prompt the LLM to write a question using terminology from B and C that only chunk A answers.

**Filtering synthetic questions:** Rate synthetic queries for realism using few-shot LLM scoring. Keep only those rated realistic.

### Running RAG Experiments in Opik

Use `opik.evaluation.evaluate()` to run RAG experiments:

```python
from opik.evaluation.metrics import ContextRecall, ContextPrecision, Faithfulness

metrics = [
    ContextRecall(),
    ContextPrecision(),
    Faithfulness(),
]

opik.evaluation.evaluate(
    experiment_name="rag-eval-v1",
    dataset=dataset,
    task=rag_pipeline_task,
    scoring_metrics=metrics,
    project_name="my-project",
)
```

Compare experiments side-by-side in the Opik UI to track improvements across retrieval and generation changes.

### Evaluating Generation Quality

After confirming retrieval works, evaluate what the LLM does with the retrieved context along two dimensions:

**Answer faithfulness:** Does the output accurately reflect the retrieved context? Check for:
- **Hallucinations:** Information absent from source documents. In RAG, even correct facts from the LLM's own knowledge count as hallucinations. Use Opik's `Hallucination` metric.
- **Omissions:** Relevant information from the context ignored in the output.
- **Misinterpretations:** Context information represented inaccurately.

Use Opik's `Faithfulness` metric to measure this automatically.

**Answer relevance:** Does the output address the original query? An answer can be faithful to the context but fail to answer what the user asked. Use Opik's `AnswerRelevance` metric.

Use error analysis to discover specific manifestations in your pipeline. Identify what kind of information gets hallucinated and which constraints get omitted.

#### Diagnosing Failures by Metric Pattern

| Context Relevance | Faithfulness | Answer Relevance | Diagnosis |
|---|---|---|---|
| High | High | Low | Generator attended to wrong section of a correct document |
| High | Low | -- | Hallucination or misinterpretation of retrieved content |
| Low | -- | -- | Retrieval problem. Fix chunking, embeddings, or query preprocessing |

## Anti-Patterns

- Using a single end-to-end correctness metric without separating retrieval and generation measurement.
- Jumping directly to metrics without reading traces first.
- Overfitting to synthetic evaluation data. Validate against real user queries regularly.
- Evaluating generation without checking context grounding.
