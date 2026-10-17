# LLM Observability Core Concepts

Keep this mental model in mind when integrating Opik into an agent or LLM app.

## Trace Boundaries

A trace should represent one complete interaction or workflow run.

Good defaults:

- one user message -> one agent response
- one API request -> one API response
- one workflow execution -> one trace

Avoid:

- one trace across multiple chat turns
- unrelated work in the same trace
- overly granular traces when spans are enough

## Spans

Spans are the steps inside a trace. Use them to break the run into meaningful operations.

Valid span types:

- `general`: orchestration or custom logic
- `llm`: model calls
- `tool`: tools, retrieval, external calls
- `guardrail`: validation or safety checks

Integration rule:

- use descriptive names
- set the right span type
- end spans when the operation completes

## Threads

Use `thread_id` to group multiple traces into one conversation or multi-step workflow.

Use threads for:

- chat agents
- support conversations
- workflows that continue across multiple user interactions

Do not use one trace for multiple turns. Use one trace per turn and share the same `thread_id`.

For concrete usage, see `SKILL.md` and `references/tracing-python.md`.

## Metadata and Tags

Use metadata and tags to make traces filterable and comparable later.

Good metadata examples:

- user or session identifiers
- environment
- experiment or rollout variant
- model version
- business context

Good tag examples:

- `production`
- `customer-support`
- `rag`
- `needs-review`

## Feedback Scores

Feedback scores are useful when you want to attach quality signals to traces.

Common sources:

- user feedback
- human review
- automated evaluation
- heuristics or post-processing

For concrete trace update patterns, see `references/tracing-python.md`.

## Distributed Tracing

If one request crosses service boundaries, propagate trace headers so downstream work stays connected to the same overall trace.

For concrete propagation examples with `get_distributed_trace_headers()` and `distributed_headers`, see `references/tracing-python.md`.

## Sensitive Data

Be careful about what you log.

- do not log secrets or credentials
- avoid raw PII unless you explicitly need it
- prefer anonymization or selective logging for sensitive data
