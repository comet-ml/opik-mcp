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
| `--transport` | - | `stdio` or `streamable-http` | `stdio` |
| `--streamableHttpPort` | - | streamable-http port | `3001` |
| `--streamableHttpHost` | - | streamable-http host | `127.0.0.1` |
| `--streamableHttpLogPath` | - | streamable-http log file path | `/tmp/opik-mcp-streamable-http.log` |
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

- `TRANSPORT` (`stdio` or `streamable-http`)
- `STREAMABLE_HTTP_PORT`
- `STREAMABLE_HTTP_HOST`
- `STREAMABLE_HTTP_LOG_PATH`

### Remote transport auth settings

- `STREAMABLE_HTTP_REQUIRE_AUTH` (default `true`)
- `STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH` (default `true`, except test env)
- `REMOTE_TOKEN_WORKSPACE_MAP` (JSON token -> workspace map)
- `STREAMABLE_HTTP_TRUST_WORKSPACE_HEADERS` (default `false`)
- `STREAMABLE_HTTP_CORS_ORIGINS` (comma-separated CORS allowlist)
- `STREAMABLE_HTTP_RATE_LIMIT_WINDOW_MS` (default `60000`)
- `STREAMABLE_HTTP_RATE_LIMIT_MAX` (default `120`)

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

TRANSPORT=streamable-http
STREAMABLE_HTTP_PORT=3001
STREAMABLE_HTTP_REQUIRE_AUTH=true
STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH=true
REMOTE_TOKEN_WORKSPACE_MAP={"token-a":"workspace-a","token-b":"workspace-b"}
STREAMABLE_HTTP_TRUST_WORKSPACE_HEADERS=false
STREAMABLE_HTTP_CORS_ORIGINS=https://example.com,https://app.example.com
STREAMABLE_HTTP_RATE_LIMIT_WINDOW_MS=60000
STREAMABLE_HTTP_RATE_LIMIT_MAX=120

OPIK_TOOLSETS=core,expert-prompts,expert-datasets,expert-trace-actions,metrics
```
