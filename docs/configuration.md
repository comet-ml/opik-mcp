# Configuration

This document covers runtime configuration for the Opik MCP server.

## Configuration Sources

Priority order:

1. CLI arguments
2. Environment variables
3. `~/.opik.config` (when present)
4. Built-in defaults

## CLI Arguments

```bash
node build/index.js \
  --apiUrl "https://www.comet.com/opik/api" \
  --apiKey "your-api-key" \
  --workspace "default" \
  --toolsets core,expert-prompts
```

| Argument | Alias | Description | Default |
| --- | --- | --- | --- |
| `--apiUrl` | `--url` | Opik API base URL | `https://www.comet.com/opik/api` |
| `--apiKey` | `--key` | Opik API key | empty |
| `--workspace` | `--ws` | Default workspace name | `default` |
| `--selfHosted` | - | Self-hosted mode flag | `false` |
| `--debug` | - | Debug logging | `false` |
| `--transport` | - | `stdio` or `sse` | `stdio` |
| `--ssePort` | - | SSE port | `3001` |
| `--sseHost` | - | SSE host (informational) | `localhost` |
| `--sseLogPath` | - | SSE log file path | `/tmp/opik-mcp-sse.log` |
| `--mcpName` | - | Server name | `opik-manager` |
| `--mcpVersion` | - | Server version | `1.0.0` |
| `--mcpPort` | - | Optional MCP port metadata | unset |
| `--mcpLogging` | - | MCP logging flag | `false` |
| `--mcpDefaultWorkspace` | - | Fallback workspace | `default` |
| `--toolsets` | - | Enabled toolsets | `core` |

## Environment Variables

### Core API settings

- `OPIK_API_BASE_URL`
- `OPIK_API_KEY`
- `OPIK_WORKSPACE_NAME`
- `OPIK_SELF_HOSTED`
- `DEBUG_MODE`

### Transport settings

- `TRANSPORT` (`stdio` or `sse`)
- `SSE_PORT`
- `SSE_HOST`
- `SSE_LOG_PATH`

### Remote SSE auth settings

- `SSE_REQUIRE_AUTH` (default `true`)
- `SSE_VALIDATE_REMOTE_AUTH` (default `true`, except test env)

### MCP settings

- `MCP_NAME`
- `MCP_VERSION`
- `MCP_PORT`
- `MCP_LOGGING`
- `MCP_DEFAULT_WORKSPACE`

### Toolset settings

- `OPIK_TOOLSETS` (comma-separated)

## Toolsets

Current toolsets:

- `core` - day-to-day read tools and capabilities
- `expert-prompts` - prompt management
- `expert-datasets` - dataset management
- `expert-trace-actions` - advanced trace actions
- `expert-project-actions` - project mutations
- `integration` - integration/reference helpers
- `metrics` - metrics tools

Legacy aliases (accepted for compatibility):

- `capabilities` -> `core`
- `prompts` -> `expert-prompts`
- `datasets` -> `expert-datasets`
- `projects` -> `core,expert-project-actions`
- `traces` -> `core,expert-trace-actions`

## Example `.env`

```dotenv
OPIK_API_BASE_URL=https://www.comet.com/opik/api
OPIK_API_KEY=your-api-key
OPIK_WORKSPACE_NAME=default

TRANSPORT=sse
SSE_PORT=3001
SSE_REQUIRE_AUTH=true
SSE_VALIDATE_REMOTE_AUTH=true

OPIK_TOOLSETS=core,expert-prompts,expert-datasets,expert-trace-actions,metrics
```
