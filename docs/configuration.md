# Configuration

This document provides detailed information about configuring the Opik MCP server.

## Configuration Methods

The MCP server can be configured through environment variables (`.env` file) or command-line arguments. Command-line arguments take precedence over environment variables.

### Command-line Arguments

Run the server with command-line arguments:

```bash
node build/index.js --apiUrl "https://www.comet.com/opik/api" --apiKey "your-api-key" --workspace "default" --toolsets capabilities,prompts,projects
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
| `--toolsets` | - | Comma-separated list of toolsets to enable | "capabilities,datasets,prompts,projects,traces" |

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

#### Toolset Configuration
- `OPIK_TOOLSETS`: Comma-separated list of toolsets to enable (defaults to "capabilities,datasets,prompts,projects,traces")

**Available Toolsets:**
- `capabilities`: Server info and help tools
- `integration`: Integration documentation and guides
- `datasets`: Dataset and evaluation data management tools
- `prompts`: Prompt management tools
- `projects`: Project/workspace management tools
- `traces`: Trace listing and analysis tools
- `metrics`: Metrics and analytics tools

**Toolset Benefits:**
- **Focused functionality**: Enable only the tools you need
- **Reduced context size**: Fewer tools for better AI performance
- **Faster startup**: Only load necessary components
- **Cleaner interface**: Less overwhelming for users

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

# Toolset Configuration (optional - uses defaults if not specified)
OPIK_TOOLSETS=capabilities,datasets,prompts,projects,traces
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

# Toolset Configuration
OPIK_TOOLSETS=capabilities,datasets,prompts,projects,traces,metrics
```

## Common Toolset Configurations

### Minimal Setup (Getting Started)
For users just starting with Opik:
```bash
--toolsets capabilities,prompts
```
or
```dotenv
OPIK_TOOLSETS=capabilities,prompts,datasets
```

### Development & Debugging
For developers working with LLM applications:
```bash
--toolsets capabilities,datasets,prompts,projects,traces
```
or
```dotenv
OPIK_TOOLSETS=capabilities,datasets,prompts,projects,traces
```

### Production Monitoring
For production systems focusing on observability:
```bash
--toolsets capabilities,traces,metrics
```
or
```dotenv
OPIK_TOOLSETS=capabilities,traces,metrics
```

### Full Integration Setup
For comprehensive integration workflows:
```bash
--toolsets integration,capabilities,datasets,prompts,projects
```
or
```dotenv
OPIK_TOOLSETS=integration,capabilities,datasets,prompts,projects
```

### Complete Feature Set
To enable all available toolsets:
```bash
--toolsets capabilities,integration,datasets,prompts,projects,traces,metrics
```
or
```dotenv
OPIK_TOOLSETS=capabilities,integration,datasets,prompts,projects,traces,metrics
```
