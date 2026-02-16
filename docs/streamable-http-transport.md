# Streamable HTTP Transport

This document describes remote/self-hosted transport for `opik-mcp`.

## Scope

- This repository ships the MCP server implementation.
- There is no managed hosted Opik remote MCP service in this repo.
- You run this server yourself and expose it behind your own network/security controls.

## Endpoint

- `GET /health`
- `POST|GET|DELETE /mcp` (MCP Streamable HTTP)

Legacy endpoints:

- `/events` and `/send` are removed and return `410`.

## Auth and Tenant Routing

Remote mode is fail-closed by default.

- Require auth on `/mcp`.
- Supported auth headers:
  - `Authorization: Bearer <token>`
  - `x-api-key: <token>`
- Missing auth returns `401`.
- Invalid key/workspace returns `401` (when validation is enabled).

Workspace resolution is server-side:

1. If `REMOTE_TOKEN_WORKSPACE_MAP` is configured, token must be present in the map and mapped workspace is used.
2. Else fallback is server default workspace.
3. Header workspace is ignored by default unless `SSE_TRUST_WORKSPACE_HEADERS=true`.

When a request context workspace is resolved, tool-level `workspaceName` arguments are ignored.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `SSE_REQUIRE_AUTH` | `true` | Require auth headers on `/mcp` |
| `SSE_VALIDATE_REMOTE_AUTH` | `true` (except test env) | Validate token/workspace against Opik before processing MCP requests |
| `REMOTE_TOKEN_WORKSPACE_MAP` | unset | JSON object mapping API token -> workspace |
| `SSE_TRUST_WORKSPACE_HEADERS` | `false` | Trust `Comet-Workspace`/`x-workspace-name`/`x-opik-workspace` headers when no token map is configured |
| `SSE_CORS_ORIGINS` | unset | Comma-separated CORS allowlist |
| `SSE_RATE_LIMIT_WINDOW_MS` | `60000` | Rate-limit window |
| `SSE_RATE_LIMIT_MAX` | `120` | Max requests per key/path per window |

## Verification

```bash
npm run build
SSE_REQUIRE_AUTH=true SSE_VALIDATE_REMOTE_AUTH=true npm run start:sse
```

Health:

```bash
curl -s http://localhost:3001/health
```

Authenticated MCP request:

```bash
curl -i -X POST http://localhost:3001/mcp \
  -H "content-type: application/json" \
  -H "Authorization: Bearer <OPIK_API_KEY>" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list","params":{}}'
```

Unauthenticated request:

```bash
curl -i -X POST http://localhost:3001/mcp \
  -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"tools/list","params":{}}'
```

Expected: `401`.
