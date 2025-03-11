# Configuration

This document provides detailed information about configuring the Opik MCP server.

## Configuration Methods

The MCP server can be configured through environment variables (`.env` file) or command-line arguments. Command-line arguments take precedence over environment variables.

### Command-line Arguments

Run the server with command-line arguments:

```bash
node build/index.js --apiUrl "https://www.comet.com/opik/api" --apiKey "your-api-key" --workspace "default"
```

> **Important**: The workspace name should typically be "default" unless you have explicitly created additional workspaces. Do not use project names (like "Therapist Chat") as workspace names, as this will result in API errors.

#### Available Arguments

| Argument | Alias | Description | Default |
|----------|-------|-------------|---------|
| `--apiUrl` | `-url` | API base URL | - |
| `--apiKey` | `-key` | API key for authentication | - |
| `--workspace` | `-ws` | Workspace name (typically "default") | "default" |
| `--selfHosted` | - | Whether the instance is self-hosted | false |
| `--debug` | - | Enable debug mode | false |
| `--mcpName` | - | MCP server name | "opik-manager" |
| `--mcpVersion` | - | MCP server version | "1.0.0" |
| `--mcpPort` | - | MCP server port | - |
| `--mcpLogging` | - | Enable MCP server logging | false |
| `--mcpDefaultWorkspace` | - | Default workspace name | "default" |
| `--disablePromptTools` | - | Disable prompt-related tools | false |
| `--disableProjectTools` | - | Disable project-related tools | false |
| `--disableTraceTools` | - | Disable trace-related tools | false |
| `--disableMetricTools` | - | Disable metric-related tools | false |

### Environment Variables

Alternatively, configure via environment variables in a `.env` file:

#### Common Configuration
- `OPIK_API_BASE_URL`: The base URL for the API
  - For cloud: "https://comet.com/opik/api"
  - For self-hosted: "http://localhost:5173/api"
- `OPIK_API_KEY`: Your API key for authentication
- `OPIK_SELF_HOSTED`: Set to "true" for self-hosted instances, or "false" for cloud (default is "false")
- `DEBUG_MODE`: Set to "true" to see detailed API request logs (default is "false")

#### Cloud-specific Configuration
- `OPIK_WORKSPACE_NAME`: Your workspace name (typically "default" for most users)

#### MCP Server Configuration
- `MCP_NAME`: Name of the MCP server (defaults to "opik-manager")
- `MCP_VERSION`: Version of the MCP server (defaults to "1.0.0")
- `MCP_PORT`: Optional port for TCP connections if needed
- `MCP_LOGGING`: Set to "true" to enable MCP-specific logging (defaults to "false")
- `MCP_DEFAULT_WORKSPACE`: Default workspace to use if none is specified (defaults to "default")
- `MCP_TRANSPORT`: Transport to use, either "stdio" or "sse" (defaults to "stdio")
- `MCP_SSE_PORT`: Port to use for SSE transport (defaults to 3001)

#### Tool Enablement
- `MCP_ENABLE_PROMPT_TOOLS`: Set to "false" to disable prompt-related tools (defaults to "true")
- `MCP_ENABLE_PROJECT_TOOLS`: Set to "false" to disable project-related tools (defaults to "true")
- `MCP_ENABLE_TRACE_TOOLS`: Set to "false" to disable trace-related tools (defaults to "true")
- `MCP_ENABLE_METRIC_TOOLS`: Set to "false" to disable metric-related tools (defaults to "true")

## Example Configuration

### Basic Configuration

```dotenv
# API Configuration
OPIK_API_BASE_URL=https://www.comet.com/opik/api
OPIK_API_KEY=your-api-key
OPIK_WORKSPACE_NAME=default

# MCP Server Configuration
MCP_NAME=opik-manager
MCP_VERSION=1.0.0
MCP_TRANSPORT=stdio
```

### Advanced Configuration

```dotenv
# API Configuration
OPIK_API_BASE_URL=https://www.comet.com/opik/api
OPIK_API_KEY=your-api-key
OPIK_WORKSPACE_NAME=default
OPIK_SELF_HOSTED=false
DEBUG_MODE=true

# MCP Server Configuration
MCP_NAME=custom-mcp-server
MCP_VERSION=2.0.0
MCP_TRANSPORT=sse
MCP_SSE_PORT=3005
MCP_LOGGING=true
MCP_DEFAULT_WORKSPACE=default

# Tool Enablement
MCP_ENABLE_PROMPT_TOOLS=true
MCP_ENABLE_PROJECT_TOOLS=true
MCP_ENABLE_TRACE_TOOLS=true
MCP_ENABLE_METRIC_TOOLS=true
```
