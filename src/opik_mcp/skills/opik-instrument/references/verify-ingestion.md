---
last_updated: "2026-09-10"
source_commit: "2.0.0"
---

# Verifying ingestion

## SDK check

```python
import opik

client = opik.Opik()

tid = "<trace_id>"  # the tracer logs a trace URL/id when the run flushes — use that
detail = client.get_trace_content(
    tid
)  # TracePublic: exposes project_id, NOT project_name (accessing .project_name raises)
project = client.rest_client.projects.get_project_by_id(detail.project_id).name
spans = client.search_spans(
    project_name=project, trace_id=tid
)  # SEPARATE call; without project_name it searches the default project and returns nothing
# Reconstruct the tree via each span's parent_span_id (the root span has none); check the expected types (general -> tool/llm).
```

## Common ingestion traps

**Common ingestion traps.** If the trace is empty, partial, or has unnamed spans, it is almost always one of these — not a bug in the instrumentation you added:
- **Batching race on fast spans.** With batching on, a span created and ended within one flush window can be reordered by the backend and dropped (or stripped of its name). Ensure a single `flush()` at the very end and allow a few seconds before verifying.
- **Post-hoc scores dropped.** A feedback score attached *after* a span closes (`log_traces_feedback_scores` / `log_spans_feedback_scores` by id) is lost if the span's create hasn't reached the backend yet — flush before posting the score, and reference the span by id.
- **Missing flush.** The most common empty-trace cause: a script that exits without `opik.flush_tracker()` / `await client.flush()`.
