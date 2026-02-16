# Streamable HTTP Transport

This document describes remote/self-hosted transport for `opik-mcp`.

## Scope

- This repository ships the MCP server implementation.
- There is no managed hosted Opik remote MCP service in this repo.
- You run this server yourself and expose it behind your own network/security controls.

## Endpoints

- `GET /health`
- `POST|GET|DELETE /mcp` (MCP Streamable HTTP)

## Auth and Tenant Routing

Remote mode is fail-closed by default.

- Require auth on `/mcp`.
- Supported auth headers:
  - `Authorization: Bearer <token>`
  - `x-api-key: <token>`
- Missing auth returns `401`.
- Invalid key/workspace returns `401` (when validation is enabled).
- If `REMOTE_TOKEN_WORKSPACE_MAP` is configured and token is not mapped, request returns `403`.
- OAuth discovery/registration endpoints are not implemented; this server uses direct API-key bearer auth.

Workspace resolution is server-side:

1. If `REMOTE_TOKEN_WORKSPACE_MAP` is configured, token must be present in the map and mapped workspace is used.
2. Else fallback is server default workspace.
3. Header workspace is ignored by default unless `STREAMABLE_HTTP_TRUST_WORKSPACE_HEADERS=true`.

When a request context workspace is resolved, tool-level `workspaceName` arguments are ignored.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `STREAMABLE_HTTP_REQUIRE_AUTH` | `true` | Require auth headers on `/mcp` |
| `STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH` | `true` (except test env) | Validate token/workspace against Opik before processing MCP requests |
| `REMOTE_TOKEN_WORKSPACE_MAP` | unset | JSON object mapping API token -> workspace |
| `STREAMABLE_HTTP_TRUST_WORKSPACE_HEADERS` | `false` | Trust `Comet-Workspace`/`x-workspace-name`/`x-opik-workspace` headers when no token map is configured |
| `STREAMABLE_HTTP_CORS_ORIGINS` | unset | Comma-separated CORS allowlist |
| `STREAMABLE_HTTP_RATE_LIMIT_WINDOW_MS` | `60000` | Rate-limit window |
| `STREAMABLE_HTTP_RATE_LIMIT_MAX` | `120` | Max requests per key/path per window |

## Verification

```bash
npm run build
STREAMABLE_HTTP_REQUIRE_AUTH=true STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH=true npm run start:http
```

Health:

```bash
curl -s http://127.0.0.1:3001/health
```

Initialize (capture `mcp-session-id` response header):

```bash
curl -i -X POST http://127.0.0.1:3001/mcp \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "mcp-protocol-version: 2024-11-05" \
  -H "Authorization: Bearer <OPIK_API_KEY>" \
  -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0.0"}}}'
```

Then send follow-up requests with the returned session header:

```bash
curl -i -X POST http://127.0.0.1:3001/mcp \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "mcp-protocol-version: 2024-11-05" \
  -H "Authorization: Bearer <OPIK_API_KEY>" \
  -H "mcp-session-id: <SESSION_ID_FROM_INITIALIZE>" \
  -d '{"jsonrpc":"2.0","id":"2","method":"tools/list","params":{}}'
```

Resource discovery example:

```bash
curl -i -X POST http://127.0.0.1:3001/mcp \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "mcp-protocol-version: 2024-11-05" \
  -H "Authorization: Bearer <OPIK_API_KEY>" \
  -H "mcp-session-id: <SESSION_ID_FROM_INITIALIZE>" \
  -d '{"jsonrpc":"2.0","id":"3","method":"resources/templates/list","params":{}}'
```

Unauthenticated request:

```bash
curl -i -X POST http://127.0.0.1:3001/mcp \
  -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"tools/list","params":{}}'
```

Expected: `401`.
