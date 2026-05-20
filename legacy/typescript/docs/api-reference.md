# API Reference

This document provides detailed information about all the available tools and endpoints in the Opik MCP server.

## Architecture Overview

The Opik API follows a hierarchical structure:
- **Workspaces** are top-level containers that organize all resources (typically "default" for most users)
- **Projects** exist within workspaces and are used to group related traces (e.g., "Therapist Chat", "Demo chatbot 🤖")
- **Traces** are always associated with a specific project

> **Important Note**: Do not confuse workspaces with projects. The workspace name is typically "default" unless you've explicitly created additional workspaces. Project names like "Therapist Chat" are not valid workspace names and will cause API errors if used as such.

### Project Name Mapping

When using the MCP server, you can work with specific projects in several ways:

1. **Project ID**: Most tools accept a `projectId` parameter to specify which project to use
2. **Project Name**: Many tools also accept a `projectName` parameter as an alternative to using the ID
3. **Default Behavior**: If neither is provided, the server will automatically use the first available project in your workspace

For example, when listing traces, you can specify:
```typescript
// Using project ID
{
  name: "list-traces",
  parameters: {
    page: 1,
    size: 10,
    projectId: "0194fdd8-de46-73c4-b0ac-381cec5fbf5c"  // Specific project ID
  }
}

// Using project name
{
  name: "list-traces",
  parameters: {
    page: 1,
    size: 10,
    projectName: "Therapist Chat"  // Project name is automatically mapped to the correct ID
  }
}
```

## Toolset Configuration

The Opik MCP server organizes tools into **toolsets** - logical groups of related functionality. This allows you to enable only the tools you need, reducing complexity and improving performance.

### Available Toolsets

| Toolset | Description | Tools Included |
|---------|-------------|----------------|
| `core` | Day-to-day read-oriented operations | Server info/help, project reads, trace reads, workflow prompts |
| `integration` | Integration documentation and guides | Step-by-step integration workflows |
| `expert-datasets` | Dataset and evaluation data management | Create/list datasets and manage dataset items |
| `expert-prompts` | Prompt management tools | Create, retrieve, and version prompts |
| `expert-project-actions` | Project mutation tools | Create project |
| `expert-trace-actions` | Advanced trace actions | Search traces and add trace feedback |
| `metrics` | Metrics and analytics tools | Performance and usage metrics |

### Default Configuration

By default, the following toolsets are enabled:
- `core`

### Configuring Toolsets

You can control which toolsets are enabled using:

**Command Line:**
```bash
--toolsets all
```

**Environment Variable:**
```bash
export OPIK_TOOLSETS=all
```

See the [Configuration Guide](./configuration.md) for detailed examples and common configurations.

## Available Tools

> **Note**: Tools are organized by toolsets. To use tools from a specific category, ensure the corresponding toolset is enabled in your configuration.

### Prompts
*Requires the `expert-prompts` toolset to be enabled*

#### 1. Get Prompts

Lists all available prompts with optional filtering and pagination support.

```typescript
{
  name: "get-prompts",
  parameters: {
    page?: number,      // Page number for pagination (default: 1)
    size?: number,      // Number of items per page (default: 10)
    name?: string       // Optional filter by prompt name
  }
}
```

#### 2. Create Prompt

Creates a new prompt with optional description and tags.

```typescript
{
  name: "create-prompt",
  parameters: {
    name: string,           // Name of the prompt (required, min 1 character)
    description?: string,   // Optional description of the prompt
    tags?: string[]         // Optional list of tags for the prompt
  }
}
```

#### 3. Get Prompt Version

Retrieves a specific version of a prompt by name and optional commit.

```typescript
{
  name: "get-prompt-version",
  parameters: {
    name: string,       // Name of the prompt (required, min 1 character)
    commit?: string     // Optional specific commit/version to retrieve
  }
}
```

#### 4. Get Prompt by ID

Retrieves a prompt by ID.

```typescript
{
  name: "get-prompt-by-id",
  parameters: {
    promptId: string
  }
}
```

#### 5. Delete Prompt

Deletes a prompt by ID.

```typescript
{
  name: "delete-prompt",
  parameters: {
    promptId: string
  }
}
```

#### 6. Save Prompt Version

Saves a new version of a prompt with template content and metadata.

```typescript
{
  name: "save-prompt-version",
  parameters: {
    name: string,                      // Name of the prompt (required, min 1 character)
    template: string,                  // Template content for the prompt version
    changeDescription?: string,        // Optional description of changes in this version
    change_description?: string,       // Deprecated alias for changeDescription
    metadata?: Record<string, any>,    // Optional additional metadata
    type?: "mustache" | "jinja2"      // Optional template type
  }
}
```

## Resources

The server also supports MCP resources:

- `resources/list` for static URIs (for example `opik://workspace-info`, `opik://projects-list`)
- `resources/templates/list` for dynamic templates (for example `opik://projects/{page}/{size}`, `opik://prompt/{name}`)
- `resources/read` for both static resources and filled template URIs

### Datasets
*Requires the `datasets` toolset to be enabled*

#### 1. List Datasets

Lists datasets with optional pagination and name filtering.

```typescript
{
  name: "list-datasets",
  parameters: {
    page?: number,
    size?: number,
    name?: string,
    workspaceName?: string
  }
}
```

#### 2. Get Dataset by ID

```typescript
{
  name: "get-dataset-by-id",
  parameters: {
    datasetId: string,
    workspaceName?: string
  }
}
```

#### 3. Create Dataset

```typescript
{
  name: "create-dataset",
  parameters: {
    name: string,
    description?: string,
    workspaceName?: string
  }
}
```

#### 4. Delete Dataset

```typescript
{
  name: "delete-dataset",
  parameters: {
    datasetId: string,
    workspaceName?: string
  }
}
```

#### 5. List Dataset Items

```typescript
{
  name: "list-dataset-items",
  parameters: {
    datasetId: string,
    page?: number,
    size?: number,
    workspaceName?: string
  }
}
```

#### 6. Create Dataset Item

```typescript
{
  name: "create-dataset-item",
  parameters: {
    datasetId: string,
    input: Record<string, any>,
    expectedOutput?: Record<string, any>,
    metadata?: Record<string, any>,
    workspaceName?: string
  }
}
```

#### 7. Delete Dataset Item

```typescript
{
  name: "delete-dataset-item",
  parameters: {
    itemId: string,
    workspaceName?: string
  }
}
```

### Projects/Workspaces
*Requires the `projects` toolset to be enabled*

#### 1. List Projects

Lists all available projects with optional filtering and pagination support.

```typescript
{
  name: "list-projects",
  parameters: {
    page?: number,                // Page number for pagination (default: 1)
    size?: number,                // Number of items per page (default: 10)
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 2. Create Project

Creates a new project with optional description.

```typescript
{
  name: "create-project",
  parameters: {
    name: string,                 // Name for the new project (required, min 1 character)
    description?: string,         // Optional description for the new project
    workspaceName?: string        // Optional workspace name override
  }
}
```

### Traces
*Requires the `traces` toolset to be enabled*

> **Important**: The Opik API requires either a project ID or project name for most trace endpoints. You can specify either:
> - `projectId`: The unique identifier of the project (e.g., "0194fdd8-de46-73c4-b0ac-381cec5fbf5c")
> - `projectName`: The human-readable name of the project (e.g., "Therapist Chat")
>
> If neither is provided, the MCP server will automatically use the first available project in your workspace.

#### 1. List Traces

Lists all available traces with pagination and filtering support. Use this for basic trace retrieval and overview.

```typescript
{
  name: "list-traces",
  parameters: {
    page?: number,                // Page number for pagination (default: 1, starts at 1)
    size?: number,                // Number of traces per page (default: 10, max 100)
    projectId?: string,           // Project ID to filter traces (auto-selects first project if not provided)
    projectName?: string,         // Project name to filter traces (alternative to projectId, e.g. "My AI Assistant")
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 2. Get Trace by ID

Retrieves detailed information about a specific trace including input, output, metadata, and timing information.

```typescript
{
  name: "get-trace-by-id",
  parameters: {
    traceId: string,              // ID of the trace to fetch (UUID format, e.g. "123e4567-e89b-12d3-a456-426614174000")
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 3. Get Trace Stats

Retrieves aggregated statistics for traces including counts, costs, token usage, and performance metrics over time.

```typescript
{
  name: "get-trace-stats",
  parameters: {
    projectId?: string,           // Project ID to filter traces (auto-selects first project if not provided)
    projectName?: string,         // Project name to filter traces (alternative to projectId)
    startDate?: string,           // Start date in ISO format (YYYY-MM-DD, e.g. "2024-01-01")
    endDate?: string,             // End date in ISO format (YYYY-MM-DD, e.g. "2024-01-31")
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 4. Search Traces

Advanced search for traces with complex filtering and query capabilities.

```typescript
{
  name: "search-traces",
  parameters: {
    projectId?: string,           // Project ID to search within
    projectName?: string,         // Project name to search within
    query?: string,               // Text query to search in trace names, inputs, outputs, and metadata (e.g. "error" or "user_query:hello")
    filters?: Record<string, any>, // Advanced filters as key-value pairs (e.g. {"status": "error"}, {"model": "gpt-4"}, {"duration_ms": {"$gt": 1000}})
    page?: number,                // Page number for pagination (default: 1)
    size?: number,                // Number of traces per page (default: 10, max 100)
    sortBy?: string,              // Field to sort by: "created_at", "duration", "name", "status"
    sortOrder?: "asc" | "desc",   // Sort order: ascending or descending (default: "desc")
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 5. Get Trace Threads

Get trace threads (conversation groupings) to view related traces that belong to the same conversation or session.

```typescript
{
  name: "get-trace-threads",
  parameters: {
    projectId?: string,           // Project ID to filter threads
    projectName?: string,         // Project name to filter threads
    page?: number,                // Page number for pagination (default: 1)
    size?: number,                // Number of threads per page (default: 10)
    threadId?: string,            // Specific thread ID to retrieve (useful for getting all traces in a conversation)
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 6. Add Trace Feedback

Add feedback scores to a trace for quality evaluation and monitoring. Useful for rating trace quality, relevance, or custom metrics.

```typescript
{
  name: "add-trace-feedback",
  parameters: {
    traceId: string,              // ID of the trace to add feedback to
    scores: Array<{               // Array of feedback scores to add
      name: string,               // Name of the feedback metric (e.g. "relevance", "accuracy", "helpfulness", "quality")
      value: number,              // Numeric score value (commonly 0.0-1.0, custom scales are allowed)
      reason?: string,            // Optional explanation for the score
      source?: "ui" | "sdk" | "online_scoring", // Optional source, defaults to "sdk"
      categoryName?: string       // Optional category for grouped feedback dimensions
    }>,
    workspaceName?: string        // Optional workspace name override
  }
}
```

### Metrics
*Requires the `metrics` toolset to be enabled*

#### 1. Get Metrics

Retrieves metrics data with filtering support.

```typescript
{
  name: "get-metrics",
  parameters: {
    metricName?: string,       // Optional metric name to filter
    projectId?: string,        // Optional project ID to filter metrics
    startDate?: string,        // Optional start date in ISO format (YYYY-MM-DD)
    endDate?: string           // Optional end date in ISO format (YYYY-MM-DD)
  }
}
```

### Server Configuration
*Requires the `capabilities` toolset to be enabled*

#### 1. Get Server Info

Retrieves information about the Opik server configuration.

```typescript
{
  name: "get-server-info",
  parameters: {}
}
```

#### 2. Get Opik Help

Returns capability documentation, optionally scoped to a specific topic.

```typescript
{
  name: "get-opik-help",
  parameters: {
    topic?: "prompts" | "projects" | "traces" | "metrics" | "general"
  }
}
```

#### 3. Get Opik Examples

Returns practical examples for common Opik workflows.

```typescript
{
  name: "get-opik-examples",
  parameters: {
    task?: string  // e.g. "create prompt", "log trace", "evaluate response"
  }
}
```

#### 4. Get Opik Metrics Info

Returns metric definitions and parameter guidance.

```typescript
{
  name: "get-opik-metrics-info",
  parameters: {
    metric?: string // e.g. "hallucination", "answerrelevance", "moderation"
  }
}
```

#### 5. Get Opik Tracing Info

Returns tracing guidance by topic.

```typescript
{
  name: "get-opik-tracing-info",
  parameters: {
    topic?: "traces" | "spans" | "feedback" | "search" | "visualization"
  }
}
```

## Response Format

All tools return responses in the following format:

```typescript
{
  content: [
    {
      type: "text",
      text: string, // Response message or formatted data
    },
  ];
}
```
