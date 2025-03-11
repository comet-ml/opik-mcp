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

## Available Tools

### Prompts

#### 1. List Prompts

Lists all available prompts with pagination support.

```typescript
{
  name: "list-prompts",
  parameters: {
    page: number,    // Page number for pagination
    size: number     // Number of items per page
  }
}
```

#### 2. Create Prompt

Creates a new prompt.

```typescript
{
  name: "create-prompt",
  parameters: {
    name: string     // Name of the prompt
  }
}
```

#### 3. Create Prompt Version

Creates a new version of an existing prompt.

```typescript
{
  name: "create-prompt-version",
  parameters: {
    name: string,           // Name of the original prompt
    template: string,       // Template content for the prompt version
    commit_message: string  // Commit message for the prompt version
  }
}
```

#### 4. Get Prompt by ID

Retrieves details of a specific prompt.

```typescript
{
  name: "get-prompt-by-id",
  parameters: {
    promptId: string  // ID of the prompt to fetch
  }
}
```

#### 5. Update Prompt

Updates an existing prompt's information.

```typescript
{
  name: "update-prompt",
  parameters: {
    promptId: string,  // ID of the prompt to update
    name: string       // New name for the prompt
  }
}
```

#### 6. Delete Prompt

Deletes an existing prompt.

```typescript
{
  name: "delete-prompt",
  parameters: {
    promptId: string  // ID of the prompt to delete
  }
}
```

### Projects/Workspaces

#### 1. List Projects

Lists all available projects with pagination support.

```typescript
{
  name: "list-projects",
  parameters: {
    page: number,                 // Page number for pagination
    size: number,                 // Number of items per page
    sortBy?: string,              // Optional field to sort by
    sortOrder?: string,           // Optional sort order (asc/desc)
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 2. Get Project by ID

Retrieves details of a specific project.

```typescript
{
  name: "get-project-by-id",
  parameters: {
    projectId: string,            // ID of the project to fetch
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 3. Create Project

Creates a new project.

```typescript
{
  name: "create-project",
  parameters: {
    name: string,                 // Name for the new project
    description?: string          // Optional description for the new project
  }
}
```

#### 4. Update Project

Updates an existing project.

```typescript
{
  name: "update-project",
  parameters: {
    projectId: string,            // ID of the project to update
    name?: string,                // Optional new name for the project
    description?: string,         // Optional new description for the project
    workspaceName?: string        // Optional workspace name override
  }
}
```

#### 5. Delete Project

Deletes an existing project.

```typescript
{
  name: "delete-project",
  parameters: {
    projectId: string  // ID of the project to delete
  }
}
```

### Traces

> **Important**: The Opik API requires either a project ID or project name for most trace endpoints. You can specify either:
> - `projectId`: The unique identifier of the project (e.g., "0194fdd8-de46-73c4-b0ac-381cec5fbf5c")
> - `projectName`: The human-readable name of the project (e.g., "Therapist Chat")
>
> If neither is provided, the MCP server will automatically use the first available project in your workspace.

#### 1. List Traces

Lists all available traces with pagination and filtering support.

```typescript
{
  name: "list-traces",
  parameters: {
    page: number,                 // Page number for pagination
    size: number,                 // Number of items per page
    projectId?: string,           // Project ID to filter traces
    projectName?: string          // Project name to filter traces (alternative to projectId)
  }
}
```

#### 2. Get Trace by ID

Retrieves details of a specific trace.

```typescript
{
  name: "get-trace-by-id",
  parameters: {
    traceId: string  // ID of the trace to fetch
  }
}
```

#### 3. Get Trace Stats

Retrieves trace statistics.

```typescript
{
  name: "get-trace-stats",
  parameters: {
    projectId?: string,           // Project ID to filter traces
    projectName?: string,         // Project name to filter traces (alternative to projectId)
    startDate?: string,           // Optional start date in ISO format (YYYY-MM-DD)
    endDate?: string              // Optional end date in ISO format (YYYY-MM-DD)
  }
}
```

### Metrics

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

#### 1. Get Server Info

Retrieves information about the Opik server configuration.

```typescript
{
  name: "get-server-info",
  parameters: {}
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
