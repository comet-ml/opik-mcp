# Server-Sent Events (SSE) Transport

This document describes how to run `opik-mcp` remotely over HTTP using SSE.

## Scope

- This repository provides the MCP server implementation.
- There is currently no managed/hosted Opik remote MCP service from this repository.
- Remote usage means you run this server yourself (VM, container, k8s, etc.).

## Endpoints

- `GET /health` - health check
- `GET /events?clientId=<id>` - SSE event stream
- `POST /send` - MCP JSON-RPC input

## Security Defaults

SSE mode is fail-closed by default.

- Auth required on `/events` and `/send`
- Accepts either:
  - `Authorization: Bearer <token>`
  - `x-api-key: <token>`
- Workspace header support:
  - `Comet-Workspace`
  - `x-workspace-name`
  - `x-opik-workspace`
- Missing auth returns `401`
- Invalid auth/workspace returns `401` when remote validation is enabled

## Remote Auth Config

| Variable | Default | Description |
| --- | --- | --- |
| `SSE_REQUIRE_AUTH` | `true` | Require auth headers for SSE endpoints |
| `SSE_VALIDATE_REMOTE_AUTH` | `true` (except test env) | Validate token/workspace against Opik before accepting requests |

## Start Server

```bash
npm run build
npm run start:sse
```

## Local Verification

1. Start server:

```bash
OPIK_API_BASE_URL=https://www.comet.com/opik/api npm run start:sse
```

2. Verify health:

```bash
curl -s http://localhost:3001/health
```

3. Open event stream with auth:

```bash
curl -N "http://localhost:3001/events?clientId=local-1" \
  -H "Authorization: Bearer <OPIK_API_KEY>" \
  -H "Comet-Workspace: <WORKSPACE>"
```

4. Send request with auth:

```bash
curl -s -X POST http://localhost:3001/send \
  -H "content-type: application/json" \
  -H "Authorization: Bearer <OPIK_API_KEY>" \
  -H "Comet-Workspace: <WORKSPACE>" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"list-projects","arguments":{"page":1,"size":5}}}'
```

5. Negative test (missing auth):

```bash
curl -s -X POST http://localhost:3001/send \
  -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"tools/call","params":{"name":"list-projects","arguments":{"page":1,"size":5}}}'
```

Expected: HTTP `401`.

## Deployment Guidance

- Always run behind HTTPS.
- Prefer API gateway or ingress auth in front of SSE.
- Use short-lived tokens when possible.
- Restrict network exposure to trusted clients.
