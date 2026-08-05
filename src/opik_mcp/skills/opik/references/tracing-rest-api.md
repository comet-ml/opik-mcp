# REST API Tracing Guide

Direct HTTP access to Opik's tracing API for any language or platform.

## API Base URLs

**Open-Source (self-hosted):**
```
http://localhost:5173/api/v1/private/
```

**Opik Cloud:**
```
https://www.comet.com/opik/api/v1/private/
```

## Authentication

### Open-Source

No authentication required:
```bash
curl -X GET 'http://localhost:5173/api/v1/private/projects'
```

### Opik Cloud

Include workspace and API key headers:
```bash
curl -X GET 'https://www.comet.com/opik/api/v1/private/projects' \
  -H 'Accept: application/json' \
  -H 'Comet-Workspace: your-workspace-name' \
  -H 'authorization: your-api-key'
```

**Note:** The `authorization` header value does NOT include the `Bearer ` prefix.

## Creating Traces

### POST /traces

Create a new trace:

```bash
curl -X POST 'https://www.comet.com/opik/api/v1/private/traces' \
  -H 'Content-Type: application/json' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key' \
  -d '{
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "my-trace",
    "project_name": "my-project",
    "start_time": "2024-01-15T10:30:00Z",
    "input": {"query": "What is machine learning?"},
    "metadata": {"user_id": "user-123"},
    "tags": ["production", "chat"]
  }'
```

#### Trace Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string (UUID) | Yes | Unique trace identifier |
| `name` | string | Yes | Trace name |
| `project_name` | string | Yes | Project to log to |
| `start_time` | string (ISO 8601) | Yes | When trace started |
| `end_time` | string (ISO 8601) | No | When trace ended |
| `input` | object | No | Input data |
| `output` | object | No | Output data |
| `metadata` | object | No | Custom metadata |
| `tags` | array[string] | No | Tags for filtering |
| `thread_id` | string | No | Groups related traces |
| `error_info` | object | No | Error details |

### PATCH /traces/{id}

Update an existing trace:

```bash
curl -X PATCH 'https://www.comet.com/opik/api/v1/private/traces/550e8400-e29b-41d4-a716-446655440000' \
  -H 'Content-Type: application/json' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key' \
  -d '{
    "end_time": "2024-01-15T10:30:05Z",
    "output": {"response": "Machine learning is..."}
  }'
```

## Creating Spans

### POST /spans

Create a new span:

```bash
curl -X POST 'https://www.comet.com/opik/api/v1/private/spans' \
  -H 'Content-Type: application/json' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key' \
  -d '{
    "id": "660e8400-e29b-41d4-a716-446655440001",
    "trace_id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "llm-call",
    "type": "llm",
    "start_time": "2024-01-15T10:30:01Z",
    "input": {"prompt": "Explain machine learning"},
    "metadata": {"model": "gpt-4", "temperature": 0.7}
  }'
```

#### Span Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string (UUID) | Yes | Unique span identifier |
| `trace_id` | string (UUID) | Yes | Parent trace ID |
| `parent_span_id` | string (UUID) | No | Parent span ID (for nesting) |
| `name` | string | Yes | Span name |
| `type` | string | No | `general`, `llm`, `tool`, `guardrail` |
| `start_time` | string (ISO 8601) | Yes | When span started |
| `end_time` | string (ISO 8601) | No | When span ended |
| `input` | object | No | Input data |
| `output` | object | No | Output data |
| `metadata` | object | No | Custom metadata |
| `tags` | array[string] | No | Tags for filtering |
| `model` | string | No | Model name (LLM spans) |
| `provider` | string | No | Provider name (LLM spans) |
| `usage` | object | No | Token usage (LLM spans) |

### POST /spans/batch

Create multiple spans efficiently:

```bash
curl -X POST 'https://www.comet.com/opik/api/v1/private/spans/batch' \
  -H 'Content-Type: application/json' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key' \
  -d '{
    "spans": [
      {
        "id": "span-1",
        "trace_id": "trace-1",
        "name": "retrieve-docs",
        "type": "tool",
        "start_time": "2024-01-15T10:30:01Z",
        "end_time": "2024-01-15T10:30:02Z"
      },
      {
        "id": "span-2",
        "trace_id": "trace-1",
        "name": "generate-response",
        "type": "llm",
        "start_time": "2024-01-15T10:30:02Z",
        "end_time": "2024-01-15T10:30:04Z"
      }
    ]
  }'
```

## Logging Feedback Scores

### POST /traces/feedback-scores

Add feedback scores to traces:

```bash
curl -X POST 'https://www.comet.com/opik/api/v1/private/traces/feedback-scores' \
  -H 'Content-Type: application/json' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key' \
  -d '{
    "scores": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "relevance",
        "value": 0.95,
        "reason": "Response directly answered the question",
        "project_name": "my-project"
      }
    ]
  }'
```

#### Score Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string (UUID) | Yes | Trace ID to score |
| `name` | string | Yes | Score name |
| `value` | number | Yes | Score value (0.0-1.0 recommended) |
| `reason` | string | No | Explanation for the score |
| `project_name` | string | Yes | Project name |

## Reading Traces

### GET /traces/{id}

Retrieve a specific trace:

```bash
curl -X GET 'https://www.comet.com/opik/api/v1/private/traces/550e8400-e29b-41d4-a716-446655440000' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key'
```

### GET /traces

Search traces with filters:

```bash
curl -X GET 'https://www.comet.com/opik/api/v1/private/traces?project_name=my-project&limit=100' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key'
```

## Managing Projects

### GET /projects

List all projects:

```bash
curl -X GET 'https://www.comet.com/opik/api/v1/private/projects' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key'
```

### POST /projects

Create a new project:

```bash
curl -X POST 'https://www.comet.com/opik/api/v1/private/projects' \
  -H 'Content-Type: application/json' \
  -H 'Comet-Workspace: your-workspace' \
  -H 'authorization: your-api-key' \
  -d '{
    "name": "my-new-project",
    "description": "Project for production chat agent"
  }'
```

## Complete Example

Here's a complete example of tracing an LLM call:

```bash
#!/bin/bash
WORKSPACE="your-workspace"
API_KEY="your-api-key"
BASE_URL="https://www.comet.com/opik/api/v1/private"
TRACE_ID=$(uuidgen)
SPAN_ID=$(uuidgen)
START_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Create trace
curl -X POST "$BASE_URL/traces" \
  -H "Content-Type: application/json" \
  -H "Comet-Workspace: $WORKSPACE" \
  -H "authorization: $API_KEY" \
  -d "{
    \"id\": \"$TRACE_ID\",
    \"name\": \"chat-completion\",
    \"project_name\": \"production-chat\",
    \"start_time\": \"$START_TIME\",
    \"input\": {\"message\": \"Hello, how can I help?\"}
  }"

# Create LLM span
curl -X POST "$BASE_URL/spans" \
  -H "Content-Type: application/json" \
  -H "Comet-Workspace: $WORKSPACE" \
  -H "authorization: $API_KEY" \
  -d "{
    \"id\": \"$SPAN_ID\",
    \"trace_id\": \"$TRACE_ID\",
    \"name\": \"openai-completion\",
    \"type\": \"llm\",
    \"start_time\": \"$START_TIME\",
    \"input\": {\"prompt\": \"Hello, how can I help?\"},
    \"metadata\": {\"model\": \"gpt-4\", \"temperature\": 0.7}
  }"

# ... make your LLM call ...

END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Update span with output
curl -X PATCH "$BASE_URL/spans/$SPAN_ID" \
  -H "Content-Type: application/json" \
  -H "Comet-Workspace: $WORKSPACE" \
  -H "authorization: $API_KEY" \
  -d "{
    \"end_time\": \"$END_TIME\",
    \"output\": {\"response\": \"I'm here to help! What can I do for you?\"},
    \"usage\": {\"prompt_tokens\": 10, \"completion_tokens\": 12, \"total_tokens\": 22}
  }"

# Update trace with output
curl -X PATCH "$BASE_URL/traces/$TRACE_ID" \
  -H "Content-Type: application/json" \
  -H "Comet-Workspace: $WORKSPACE" \
  -H "authorization: $API_KEY" \
  -d "{
    \"end_time\": \"$END_TIME\",
    \"output\": {\"response\": \"I'm here to help! What can I do for you?\"}
  }"

# Add feedback score
curl -X POST "$BASE_URL/traces/feedback-scores" \
  -H "Content-Type: application/json" \
  -H "Comet-Workspace: $WORKSPACE" \
  -H "authorization: $API_KEY" \
  -d "{
    \"scores\": [{
      \"id\": \"$TRACE_ID\",
      \"name\": \"response_quality\",
      \"value\": 0.9,
      \"reason\": \"Polite and helpful response\",
      \"project_name\": \"production-chat\"
    }]
  }"
```

## Error Handling

API errors return JSON with details:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid trace ID format",
    "details": {
      "field": "id",
      "expected": "UUID format"
    }
  }
}
```

Common HTTP status codes:
- `200` - Success
- `201` - Created
- `400` - Bad request (validation error)
- `401` - Unauthorized (invalid API key)
- `404` - Not found
- `429` - Rate limited
- `500` - Server error

## Best Practices

1. **Use UUIDs for IDs**: Generate proper UUIDv4 identifiers
2. **Batch when possible**: Use `/spans/batch` for multiple spans
3. **Include timestamps**: Always provide ISO 8601 timestamps
4. **Handle rate limits**: Implement exponential backoff
5. **Validate before sending**: Check required fields locally
6. **Close spans/traces**: Always update with end_time
