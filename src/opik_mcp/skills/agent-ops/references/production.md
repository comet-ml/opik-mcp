---
last_updated: "2026-04-17"
source_commit: "2.0.0"
---

# Production Monitoring Guide

Guide to monitoring, alerting, and protecting your LLM applications in production with Opik.

## Overview

Opik provides comprehensive production monitoring:
- **Dashboards** - Visualize metrics over time
- **Online evaluation** - Automatically score production traces
- **Alerts** - Get notified when metrics deviate
- **Guardrails** - Protect against risks in real-time
- **Ollie** - Opik's AI assistant for debugging traces

## Ollie (AI Debugging)

Ollie is Opik's AI assistant — it helps debug and understand your traces.

### What Ollie Does

- **Root Cause Analysis**: Automatically identifies why a trace failed or produced poor results
- **Anomaly Detection**: Flags unusual patterns in trace behavior
- **Improvement Suggestions**: Recommends prompt or configuration changes
- **Comparison Analysis**: Explains differences between successful and failed traces

### Using Ollie

In the Opik UI:
1. Open a trace or span
2. Use the **Explain** (Ollie) action for an instant analysis, then continue the conversation in the **Ollie** sidebar
3. Ask questions about the trace:
   - "Why did this trace fail?"
   - "What caused the hallucination in span X?"
   - "How can I improve this response?"
   - "Compare this to similar successful traces"

### Example Questions

| Question Type | Example |
|---------------|---------|
| Debugging | "Why did the agent stop early?" |
| Quality | "What made the response unhelpful?" |
| Performance | "Why was this trace slow?" |
| Comparison | "How does this differ from trace X?" |
| Improvement | "Suggest prompt changes to fix this" |

### Ollie for Evaluation Results

When viewing experiment results:
1. Select traces with low scores
2. Use **Explain** (Ollie) on them
3. Get AI-generated insights on:
   - Common failure patterns
   - Suggested prompt improvements
   - Dataset gaps to address

### Programmatic Access

```python
from opik import Opik

client = Opik()

# Get AI analysis for a trace
analysis = client.assist(
    trace_id="abc-123",
    question="Why did this trace produce a hallucination?"
)

print(analysis.explanation)
print(analysis.suggestions)
```

## Dashboards

Create custom views to monitor your LLM applications.

### Dashboard Types

| Type | Location | Purpose |
|------|----------|---------|
| Standalone | Dashboards page | Cross-project monitoring |
| Project | Project > Dashboards tab | Project-specific metrics |
| Experiment | Experiment comparison | Compare evaluation results |

### Available Widgets

#### Project Metrics Widget

Time-series visualization of:
- Trace/thread feedback scores
- Trace/thread counts
- Token usage
- Estimated cost
- Failed guardrails
- Duration metrics

#### Project Statistics Widget

Single-value cards showing:
- Total trace/span counts
- P50/P90/P99 duration
- Average costs
- Error counts
- Token averages

#### Experiment Metrics Widget

Compare experiments with:
- Line charts for trends
- Bar charts for distributions
- Radar charts for multi-metric comparison

### Creating Dashboards

Via UI:
1. Navigate to Dashboards page
2. Click **Create new dashboard**
3. Choose a template or start blank
4. Add sections and widgets
5. Configure filters and metrics

### Dashboard Templates

- **Performance Overview**: Traces, quality, latency, cost summary
- **Project Metrics**: Token usage, costs, feedback, guardrails
- **Experiment Insights**: Radar/bar charts for experiment comparison

## Online Evaluation

Automatically score production traces with LLM-as-Judge metrics.

### Setting Up Evaluation Rules

1. Go to Project Settings > Evaluation Rules
2. Create a new rule
3. Configure:
   - **Metric**: AnswerRelevance, Hallucination, Moderation, etc.
   - **Sampling**: All traces or percentage
   - **Filters**: Apply to specific trace types
   - **Variable mapping**: Map metric variables to trace fields (see below)

### Variable Mapping

Each metric has input variables (e.g. `input`, `output`, `context`) that must be mapped to trace fields. Opik uses dot-notation to reference nested fields.

**Default mapping** — unless the user specifies otherwise, map variables to themselves:
- `input` → `input`
- `output` → `output`

**Nested fields** — if a field is a structured object, use dot-notation to access a key:
- `output.image_data` — reads the `image_data` key from the `output` object

Never use `{{ variable }}` template syntax for variable mapping. Variables are plain string field paths, not template expressions.

### Supported Online Metrics

| Metric | Description |
|--------|-------------|
| Answer Relevance | Does response address the query? |
| Hallucination | Are there unsupported claims? |
| Moderation | Safety and policy violations |
| Custom | Define your own LLM-as-Judge |

### Tracking Scores Over Time

Once rules are active:
- Scores appear on each trace
- Dashboard shows score trends
- Filter traces by score ranges
- Set alerts on score thresholds

## Feedback Scores

### Logging Inline with Traces

```python
import opik

@opik.track
def my_agent(query: str):
    # Your logic
    response = generate_response(query)

    # Log feedback score
    opik.opik_context.update_current_trace(
        feedback_scores=[
            {
                "name": "user_feedback",
                "value": 1.0,
                "reason": "User clicked thumbs up"
            }
        ]
    )
    return response
```

### Updating Scores Later

```python
from opik import Opik

client = Opik()

# Search for traces to annotate
traces = client.search_traces(
    project_name="production",
    filters={"tags": ["needs_review"]}
)

# Add feedback scores
for trace in traces:
    client.log_traces_feedback_scores(
        scores=[{
            "id": trace.id,
            "name": "quality_score",
            "value": 0.85,
            "reason": "Reviewed by team",
            "project_name": "production"
        }]
    )
```

## Alerts

Get notified when metrics exceed thresholds.

### Setting Up Alerts

1. Go to Project Settings > Alerts
2. Create alert with:
   - **Metric**: What to monitor
   - **Condition**: Threshold and comparison
   - **Window**: Time period to evaluate
   - **Notification**: Slack, email, webhook

### Common Alert Patterns

| Alert | Metric | Condition |
|-------|--------|-----------|
| Quality drop | AnswerRelevance | Average < 0.7 over 1 hour |
| High errors | Error count | > 10 in 15 minutes |
| Cost spike | Estimated cost | > $100 in 1 hour |
| Hallucination rate | Hallucination | Average > 0.3 over 1 hour |
| Latency regression | P95 duration | > 5s over 30 minutes |

## Guardrails

Protect your application from LLM risks in real-time.

### Types of Guardrails

| Type | Method | Use Case |
|------|--------|----------|
| PII | NLP models | Detect personal information |
| Topic | Zero-shot classifier | Ensure on-topic responses |
| Custom | Your logic | Brand mentions, business rules |

### Using Guardrails (Self-hosted)

```bash
# Start guardrails backend
./opik.sh --guardrails
```

```python
from opik.guardrails import Guardrail, PII, Topic
from opik import exceptions

guardrail = Guardrail(
    guards=[
        Topic(
            restricted_topics=["finance", "health"],
            threshold=0.9
        ),
        PII(blocked_entities=["CREDIT_CARD", "SSN", "PERSON"])
    ]
)

llm_response = "Your account balance is $5,000"

try:
    guardrail.validate(llm_response)
except exceptions.GuardrailValidationFailed as e:
    print(f"Guardrail failed: {e}")
    # Handle the failure - return safe response
```

### Custom Guardrails

```python
import opik

competitor_brands = ["OpenAI", "Anthropic", "Google AI"]

def custom_guardrail(generation: str, trace_id: str) -> str:
    client = opik.Opik()

    # Start guardrail span
    span = client.span(
        name="brand_check",
        input={"generation": generation},
        type="guardrail",
        trace_id=trace_id
    )

    # Check for competitor mentions
    found = [b for b in competitor_brands if b.lower() in generation.lower()]

    if found:
        result = "failed"
        output = {"guardrail_result": result, "found_brands": found}
    else:
        result = "passed"
        output = {"guardrail_result": result}

    span.end(output=output)
    return generation if result == "passed" else "I cannot recommend competitors."
```

### Streaming Guardrails

Validate chunks in streaming responses:

```python
for chunk in llm_stream:
    try:
        guardrail.validate(chunk)
        yield chunk
    except exceptions.GuardrailValidationFailed:
        yield "[Content filtered]"
        break
```

## Data Privacy

### Anonymizers

Automatically mask sensitive data in traces:

```python
from opik import configure

configure(
    anonymizers=[
        {"type": "pii", "action": "mask"},  # Replace PII with [MASKED]
        {"type": "email", "action": "hash"}, # Hash email addresses
    ]
)
```

### Selective Logging

Control what gets logged:

```python
import opik

@opik.track(
    capture_input=True,    # Log function inputs
    capture_output=False,  # Don't log outputs (sensitive)
)
def sensitive_function(user_data: dict) -> str:
    pass
```

### Data Retention

Configure retention policies:
- Set trace TTL per project
- Automatic deletion of old traces
- Export before deletion for compliance

## Cost Tracking

### Automatic Cost Estimation

Opik automatically estimates costs based on:
- Model pricing tables
- Input/output token counts
- Provider-specific rates

### Cost Analysis

In dashboards:
- Total cost over time
- Cost per trace/operation
- Cost by model/project
- Budget alerts

### Cost Optimization

Use traces to identify:
- Expensive operations
- Unnecessary LLM calls
- Opportunities for smaller models
- Caching opportunities

## Performance Monitoring

### Key Metrics

| Metric | What It Shows |
|--------|---------------|
| P50/P90/P99 Latency | Response time distribution |
| Throughput | Traces per minute |
| Error Rate | Failed traces percentage |
| Token Usage | Input/output token trends |

### Latency Analysis

Use traces to identify:
- Slow spans (bottlenecks)
- External API latency
- Model inference time
- Processing overhead

## Error Tracking

### Automatic Error Capture

Traces automatically capture:
- Exception type and message
- Stack trace
- Span where error occurred
- Input that caused error

### Error Analysis

Dashboard features:
- Error rate over time
- Error type breakdown
- Filter to error traces
- Drill into root causes

### Error Alerting

Set alerts for:
- Error rate exceeds threshold
- Specific error types
- Error spikes

## Best Practices

### Dashboard Design

1. **Start with templates**: Customize from there
2. **Group related metrics**: One section per concern
3. **Add context**: Use markdown widgets to explain
4. **Set appropriate date ranges**: Match your monitoring cadence

### Alerting Strategy

1. **Alert on symptoms**: User-facing issues
2. **Avoid alert fatigue**: Tune thresholds carefully
3. **Escalation paths**: Different severity levels
4. **Include context**: Link to relevant dashboards

### Guardrail Deployment

1. **Start permissive**: Log, don't block initially
2. **Analyze violations**: Understand patterns
3. **Tune thresholds**: Balance safety and usability
4. **Monitor guardrail latency**: Keep it fast

### Data Governance

1. **Define retention policy**: Before going to production
2. **Classify data sensitivity**: Per project/trace type
3. **Configure anonymizers**: For PII protection
4. **Regular audits**: Review what's being logged
