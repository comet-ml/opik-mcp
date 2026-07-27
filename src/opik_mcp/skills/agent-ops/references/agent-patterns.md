---
last_updated: "2026-04-17"
source_commit: "2.0.0"
---

# Agent Architecture Patterns

Best practices for building, evaluating, and optimizing AI agents with Opik.

## The Agent Lifecycle

Building production-grade agents requires:
1. **Observability** - Understand what your agent is doing
2. **Evaluation** - Measure performance systematically
3. **Optimization** - Improve based on data

## Start with Observability

Before evaluating, make your agent's behavior transparent.

### Critical: Trace from Input

**Tracing must start at the agent entry point, before any processing begins.** This ensures:
- The exact input is captured as received
- Traces can be replayed for debugging
- Full execution context is preserved

```python
import opik

# ✅ CORRECT: @track on the entry point captures the original input
@opik.track(name="research_agent")
def agent(query: str) -> str:
    """Entry point - trace starts here with exact input"""
    plan = plan_action(query)
    results = execute_tool(plan)
    return generate_response(query, results)

# ❌ WRONG: Starting trace after preprocessing loses original input
def agent(query: str) -> str:
    processed = preprocess(query)  # Input transformation lost!
    return _traced_agent(processed)  # Trace misses original query
```

### Enabling Replay

For traces to support replay (re-running from a trace for debugging):

```python
@opik.track(name="research_agent")
def agent(query: str, config: dict = None) -> str:
    # Capture config/env state that affects execution
    opik.opik_context.update_current_trace(
        metadata={
            "config": config,
            "feature_flags": get_feature_flags(),
            "model_version": MODEL_VERSION
        }
    )
    # Now the trace has everything needed for replay
    return execute_agent(query, config)
```

### Basic Agent Tracing

```python
import opik

@opik.track
def plan_action(query: str) -> dict:
    """Agent planning step"""
    return {"action": "search", "params": {"query": query}}

@opik.track(type="tool")
def execute_tool(action: dict) -> str:
    """Tool execution"""
    if action["action"] == "search":
        return search_web(action["params"]["query"])

@opik.track
def generate_response(query: str, tool_results: str) -> str:
    """Final response generation"""
    return llm_call(f"Query: {query}\nResults: {tool_results}")

@opik.track(name="research_agent")
def agent(query: str) -> str:
    plan = plan_action(query)
    results = execute_tool(plan)
    return generate_response(query, results)
```

### What to Trace

| Component | Span Type | Key Data |
|-----------|-----------|----------|
| Planning | `general` | Reasoning steps, decisions |
| Tool calls | `tool` | Tool name, parameters, results |
| LLM calls | `llm` | Prompt, response, tokens |
| Retrieval | `tool` | Query, documents |
| Validation | `guardrail` | Check results, pass/fail |

## Evaluating Agents

Agent evaluation goes beyond final outputs—you need to assess the journey.

### End-to-End Evaluation

Evaluate the final response quality:

```python
from opik.evaluation import evaluate
from opik.evaluation.metrics import AnswerRelevance, Hallucination

def agent_task(dataset_item):
    response = agent(dataset_item["input"])
    return {"output": response}

results = evaluate(
    experiment_name="agent-e2e-v1",
    dataset=dataset,
    task=agent_task,
    scoring_metrics=[
        AnswerRelevance(),
        Hallucination()
    ]
)
```

### Step-Level Evaluation

Evaluate individual agent decisions:

#### Tool Selection Evaluation

```python
from opik.evaluation.metrics import BaseMetric, ScoreResult

class ToolSelectionQuality(BaseMetric):
    def __init__(self):
        self.name = "tool_selection_quality"

    def score(self, tool_calls, expected_tool_calls, **kwargs):
        actual = tool_calls[0]["function_name"] if tool_calls else None
        expected = expected_tool_calls[0]["function_name"] if expected_tool_calls else None

        if actual == expected:
            return ScoreResult(
                name=self.name,
                value=1.0,
                reason=f"Correct tool: {actual}"
            )
        return ScoreResult(
            name=self.name,
            value=0.0,
            reason=f"Expected {expected}, got {actual}"
        )
```

#### Trajectory Evaluation

Use `task_span` parameter for trajectory access:

```python
from opik.evaluation.metrics import BaseMetric, ScoreResult
from opik.message_processing.emulation.models import SpanModel

class StrictToolAdherenceMetric(BaseMetric):
    def __init__(self):
        self.name = "strict_tool_adherence"

    def find_tools(self, task_span: SpanModel) -> list:
        """Extract tool names from span hierarchy"""
        tools = []

        def extract(spans):
            for span in spans:
                if span.type == "tool":
                    tools.append(span.name)
                if span.spans:
                    extract(span.spans)

        if task_span.spans:
            extract(task_span.spans)
        return tools

    def score(self, task_span: SpanModel, expected_tool: list, **kwargs):
        actual = self.find_tools(task_span)

        if actual == expected_tool:
            return ScoreResult(
                name=self.name,
                value=1.0,
                reason=f"Correct trajectory: {actual}"
            )
        return ScoreResult(
            name=self.name,
            value=0.0,
            reason=f"Expected {expected_tool}, got {actual}"
        )
```

### Built-in Agent Metrics

| Metric | Description |
|--------|-------------|
| `AgentTaskCompletion` | Did the agent fulfill its task? |
| `AgentToolCorrectness` | Were tools used with correct parameters? |
| `TrajectoryAccuracy` | Did actions match expected sequence? |

## Evaluation Dataset Design

### Tool Selection Dataset

```python
dataset.insert([
    {
        "input": "What is 25 * 17?",
        "expected_tool": ["calculator"]
    },
    {
        "input": "What's the weather in Paris?",
        "expected_tool": ["weather_api"]
    },
    {
        "input": "Tell me a joke",
        "expected_tool": []  # No tool needed
    }
])
```

### Multi-Step Dataset

```python
dataset.insert([
    {
        "input": "Book a flight and hotel for NYC",
        "expected_trajectory": [
            {"tool": "search_flights", "params": {"destination": "NYC"}},
            {"tool": "search_hotels", "params": {"city": "NYC"}},
            {"tool": "book_flight"},
            {"tool": "book_hotel"}
        ]
    }
])
```

## Evaluating Different Components

### What to Evaluate

| Component | Metrics | Dataset Fields |
|-----------|---------|----------------|
| Router/Planner | Tool selection, plan quality | Expected tools/plan |
| Tools | Output accuracy, error rate | Expected tool output |
| Memory/RAG | Relevance, recall | Expected context |
| Response | Quality, hallucination | Expected response |

### Component-Specific Evaluation

```python
# Router evaluation
router_results = evaluate(
    experiment_name="router-v1",
    dataset=router_dataset,
    task=router_task,
    scoring_metrics=[ToolSelectionQuality()]
)

# Tool evaluation
tool_results = evaluate(
    experiment_name="tools-v1",
    dataset=tool_dataset,
    task=tool_task,
    scoring_metrics=[ExactMatch(), ErrorRate()]
)

# End-to-end evaluation
e2e_results = evaluate(
    experiment_name="agent-v1",
    dataset=e2e_dataset,
    task=agent_task,
    scoring_metrics=[
        AnswerRelevance(),
        AgentTaskCompletion(),
        TrajectoryAccuracy()
    ]
)
```

## Multi-Agent Systems

### Tracing Multi-Agent Workflows

```python
import opik

@opik.track(name="orchestrator")
def orchestrator(query: str) -> str:
    # Decide which agent to use
    agent_type = classify_query(query)

    if agent_type == "research":
        return research_agent(query)
    elif agent_type == "code":
        return code_agent(query)
    else:
        return general_agent(query)

@opik.track(name="research_agent")
def research_agent(query: str) -> str:
    # Research-specific logic
    pass

@opik.track(name="code_agent")
def code_agent(query: str) -> str:
    # Code-specific logic
    pass
```

### Evaluating Agent Routing

```python
class RoutingAccuracy(BaseMetric):
    def __init__(self):
        self.name = "routing_accuracy"

    def score(self, selected_agent, expected_agent, **kwargs):
        if selected_agent == expected_agent:
            return ScoreResult(
                name=self.name,
                value=1.0,
                reason=f"Correctly routed to {selected_agent}"
            )
        return ScoreResult(
            name=self.name,
            value=0.0,
            reason=f"Routed to {selected_agent}, expected {expected_agent}"
        )
```

## Reliability Patterns

### Idempotency

Ensure operations can be safely retried:

```python
@opik.track(type="tool")
def create_order(order_data: dict, idempotency_key: str) -> dict:
    """Tool with idempotency key for safe retries"""
    # Check if already processed
    existing = db.get_order_by_idempotency_key(idempotency_key)
    if existing:
        return existing

    # Process and store with key
    order = process_order(order_data)
    order["idempotency_key"] = idempotency_key
    db.save_order(order)
    return order
```

### Retry with Backoff

```python
import time
import random

def retry_with_backoff(func, max_attempts=3, base_delay=1.0):
    """Exponential backoff with jitter"""
    for attempt in range(max_attempts):
        try:
            return func()
        except TransientError as e:
            if attempt == max_attempts - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
```

### Circuit Breaker

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_time=60):
        self.failures = 0
        self.threshold = failure_threshold
        self.recovery_time = recovery_time
        self.last_failure = None
        self.state = "closed"  # closed, open, half-open

    def call(self, func):
        if self.state == "open":
            if time.time() - self.last_failure > self.recovery_time:
                self.state = "half-open"
            else:
                raise CircuitOpenError("Circuit breaker is open")

        try:
            result = func()
            if self.state == "half-open":
                self.state = "closed"
                self.failures = 0
            return result
        except Exception as e:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.threshold:
                self.state = "open"
            raise
```

## Security Patterns

### Input Validation

```python
@opik.track(name="secure_agent")
def agent(query: str) -> str:
    # Validate input before processing
    if not is_safe_input(query):
        return "I cannot process that request."

    # Sanitize external content
    context = retrieve_context(query)
    sanitized_context = sanitize_external_content(context)

    return generate_response(query, sanitized_context)

def sanitize_external_content(content: str) -> str:
    """Remove potential prompt injection from retrieved content"""
    # Strip instruction-like patterns from external data
    patterns = [r"ignore previous", r"system:", r"<\|.*\|>"]
    for pattern in patterns:
        content = re.sub(pattern, "", content, flags=re.IGNORECASE)
    return content
```

### Tool Permission Boundaries

```python
# Define allowed tools per context
TOOL_PERMISSIONS = {
    "read_only": ["search", "get_info", "list_items"],
    "write": ["search", "get_info", "list_items", "create", "update"],
    "admin": ["search", "get_info", "list_items", "create", "update", "delete"]
}

@opik.track(name="permission_aware_agent")
def agent(query: str, permission_level: str = "read_only") -> str:
    allowed_tools = TOOL_PERMISSIONS[permission_level]

    # Pass allowed tools to agent, reject others
    return execute_with_tools(query, allowed_tools)
```

## Resource Management

### Token Budgets

```python
@opik.track(name="budget_aware_agent")
def agent(query: str, max_tokens: int = 10000) -> str:
    tokens_used = 0

    while not done and tokens_used < max_tokens:
        response, tokens = llm_call_with_count(prompt)
        tokens_used += tokens

        if tokens_used > max_tokens * 0.9:
            # Approaching limit, wrap up
            return generate_summary(partial_results)

    opik.opik_context.update_current_trace(
        metadata={"tokens_used": tokens_used}
    )
    return final_response
```

### Execution Limits

```python
MAX_STEPS = 20
MAX_TOOL_CALLS = 10

@opik.track(name="bounded_agent")
def agent(query: str) -> str:
    steps = 0
    tool_calls = 0

    while not done:
        steps += 1
        if steps > MAX_STEPS:
            return "Reached maximum steps, returning partial result."

        action = plan_next_action()
        if action["type"] == "tool":
            tool_calls += 1
            if tool_calls > MAX_TOOL_CALLS:
                return "Reached tool call limit."
            execute_tool(action)
```

## Common Anti-Patterns

### Reliability Anti-Patterns

1. **Unbounded loops**: No maximum steps or circuit breaker
2. **Tool loops**: Agent repeatedly calls same tool without progress
3. **Retry storms**: Cascading failures causing exponential retries
4. **No backoff**: Immediate retries overwhelming services
5. **Silent failures**: Errors swallowed without logging

### Security Anti-Patterns

6. **Prompt injection**: User input directly in system prompts
7. **Indirect injection**: External content (web, docs) unsanitized
8. **Privilege escalation**: Agent accessing tools beyond scope
9. **Data leakage**: Sensitive info in logs or responses

### Observability Anti-Patterns

10. **Late tracing**: Trace starts after agent begins, missing input
11. **Input mismatch**: Trace input differs from actual input (breaks replay)
12. **Orphaned spans**: Spans without parent trace

### Tool Anti-Patterns

13. **Tool loops**: Agent repeatedly calls same tool
14. **Hallucinated tools**: Agent invents non-existent tools
15. **Parameter errors**: Wrong types or missing required params
16. **Inefficient paths**: Taking more steps than necessary
17. **Context loss**: Forgetting information across turns

### Detection Metrics

```python
class LoopDetection(BaseMetric):
    def __init__(self, max_repeats: int = 3):
        self.name = "loop_detection"
        self.max_repeats = max_repeats

    def score(self, task_span, **kwargs):
        tools = self.find_tools(task_span)

        # Check for repeated consecutive tools
        for i in range(len(tools) - self.max_repeats + 1):
            window = tools[i:i + self.max_repeats]
            if len(set(window)) == 1:  # All same tool
                return ScoreResult(
                    name=self.name,
                    value=0.0,
                    reason=f"Detected loop: {window[0]} repeated {self.max_repeats} times"
                )

        return ScoreResult(
            name=self.name,
            value=1.0,
            reason="No loops detected"
        )
```

## Iterative Improvement

### The Evaluation Loop

1. **Run baseline evaluation**
2. **Analyze failures** - Filter to low-scoring items
3. **Identify patterns** - What's causing failures?
4. **Make improvements**:
   - Refine system prompt
   - Improve tool descriptions
   - Add/remove tools
   - Adjust parameters
5. **Re-evaluate** - Measure impact
6. **Compare experiments** - Verify improvement
7. **Repeat**

### Comparing Experiments

In the Opik UI:
1. Go to dataset experiments
2. Select experiments to compare
3. View metric differences
4. Drill into specific failures
5. Document what changed

## Best Practices

### Observability

- **Trace from input**: Start tracing at agent entry point, not after processing
- **Capture for replay**: Include config, feature flags, model versions in trace metadata
- Trace all agent components with appropriate span types
- Add metadata for filtering and debugging
- Include tool parameters and results

### Evaluation

- Start with end-to-end metrics
- Add component-level as needed
- Build datasets from production failures
- Run evaluations before deploying changes
- Detect anti-patterns (loops, hallucinations) with custom metrics

### Reliability

- **Idempotency**: Use idempotency keys for all mutating operations
- **Retries**: Exponential backoff with jitter, capped attempts
- **Circuit breakers**: Prevent cascade failures to downstream services
- **Timeouts**: Set per-call and total execution timeouts
- **Graceful degradation**: Return partial results when limits reached

### Security

- Validate and sanitize all inputs at agent boundary
- Sanitize external content (retrieved docs, web pages) before processing
- Apply least-privilege tool access based on context
- Never log sensitive data (PII, credentials)
- Protect against indirect prompt injection

### Resource Management

- Set token budgets per request and session
- Limit maximum steps and tool calls
- Cache repeated queries
- Use appropriate model size for task complexity
- Monitor costs and set alerts

### Optimization

- Change one variable at a time
- Track experiment configurations
- Use data to guide decisions
- Monitor production performance
