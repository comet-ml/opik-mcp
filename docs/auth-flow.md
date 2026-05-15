# Auth flow — Phase 1 (local install, API-key)

How a single `OPIK_API_KEY` drives every call the MCP server makes — including `ask_ollie`.

## Headers used everywhere

```
Authorization: <OPIK_API_KEY>
Comet-Workspace: <workspace name>
```

These are the same two headers the existing `opik` Python SDK and `extensions/cursor` MCP shim already send. No new auth scheme.

## Three upstreams, one credential

| Upstream | Endpoint(s) | Auth |
|---|---|---|
| `comet-backend` | `GET /opik/ollie/compute-api-key` | `Authorization` + `Comet-Workspace` (new, see ADR-0003) |
| `opik-backend` | All REST routes used by tools/resources | `Authorization` + `Comet-Workspace` (already supported) |
| `ollie-assist` pod | `GET /health/ready`, `POST /sessions`, SSE stream | `PPAUTH=<browserAuth>` cookie returned by `comet-backend` |

`opik-mcp` is the only component holding `OPIK_API_KEY`. The pod never sees it.

## End-to-end sequence for `ask_ollie`

```
user
  │ ask_ollie(prompt="why is my eval failing?")
  ▼
opik-mcp
  │ 1. GET comet-backend/opik/ollie/compute-api-key
  │      Authorization: $OPIK_API_KEY
  │      Comet-Workspace: $COMET_WORKSPACE
  │ ◀──── 200 {computeURL, enabled:true}
  │       Set-Cookie: PPAUTH=<browserAuth>
  │
  │ 2. (cold start path)
  │      poll computeURL/health/ready
  │      Cookie: PPAUTH=...
  │      → 503 ... 503 ... 200 (up to ~120s)
  │      Meanwhile: notifications/tasks/updated → "Starting Ollie pod..."
  │
  │ 3. POST computeURL/sessions
  │      Cookie: PPAUTH=...
  │      body: {prompt, workspace, ...}
  │ ◀──── SSE stream
  │       event: thinking_delta / tool_call_* / confirm_required / message_end
  │
  │ 4. Translate SSE → MCP frames
  │      thinking_delta → notifications/progress
  │      confirm_required → elicitation/create
  │      message_end → tasks/result
  ▼
user receives streaming response in host UI
```

## Why this works — verified upstream code paths

### `comet-backend` side

`LlmEndpoint.java` (after the patch in [comet-ml/comet-backend#5555](https://github.com/comet-ml/comet-backend/pull/5555)):

- `GET /opik/ollie/compute` → cookie auth (browser callers, unchanged)
- `GET /opik/ollie/compute-api-key` → `restHelpers.getUserNameFromRestApiAuth(req)` → resolves the user from `Authorization: <api-key>`, then runs the same `provisionOlliePod(userName, workspaceName)` helper

The body returns `CodePanelComputeUrlResponse(computeURL, enabled)` and the response carries the pod's `PPAUTH` cookie via `NewCookie` headers.

Inside `provisionOlliePod`:

1. `opikAuthService.validateWorkspaceAccess(userName, workspaceName)` — confirms the user can access this workspace
2. `codePanelComputeService.getCodePanelComputeForUser(...)` — calls `codepanels` orchestrator, idempotent (returns existing pod if already provisioned)
3. Pod label = `"ollie" + userName + "_" + organizationId` → **single-user-per-pod by construction** (no multi-tenant complexity needed in Phase 1)

### `opik-backend` side

`RemoteAuthService.authenticate()` precedence is **cookie first, then `Authorization` header**. For Phase 1 (no cookie):

- The standard `Authorization: <apikey>` header is used by every existing `opik` SDK call
- Zero changes needed

### Pod side

`ollie-assist` reads `BROWSER_AUTH` env var at startup, set by `comet-backend` when the pod is provisioned to the user's session cookie. The pod accepts requests where `Cookie: PPAUTH=<browserAuth>` matches.

For Phase 1, `opik-mcp` simply forwards whatever `Set-Cookie: PPAUTH=...` came back from step 1. No JWT verifier, no key rotation, no JWKS. Phase 2 work.

## What this rules out for Phase 1

- **No OAuth.** Single API key, no DCR, no PKCE, no JWKS, no token rotation.
- **No `/oauth/mint-user-api-key`.** The user's primary API key is also their Opik API key (same DB on cloud Comet).
- **No central audit log / `mcp_*` MySQL tables.** Local process, no shared infra.
- **No per-tenant quotas.** Local process, single user.
- **No always-warm fleet.** User starts the process locally.

All of those land in Phase 2 — hosted server.

## Cold start handling

`/ollie/compute-api-key` returns immediately even if the pod is provisioning (orchestrator install is async). The pod's `/health/ready` is the gate.

Phase 1 sequence:

1. Return `CreateTaskResult{taskId, status: "in_progress"}` from `tools/call` within 2 seconds — **before** the pod is ready.
2. Background goroutine polls `/health/ready` every 2s for up to 120s.
3. Each iteration emits `notifications/tasks/updated{status: "Starting Ollie pod (15s)..."}` so the host UI shows progress.
4. Once ready → open the SSE session and start streaming `notifications/progress`.

If the host doesn't advertise `capabilities.experimental.tasks`, fall back to blocking on `tools/call` and rely on heartbeat `notifications/progress` to keep the host from timing out. Some hosts (notably Cursor < 0.43) will still time out; document the matrix in `docs/install/`.

## Self-hosted users

Self-hosted Comet without `ollie-assist` deployed:
- `comet-backend/opik/ollie/compute-api-key` returns `{computeURL: "", enabled: false}`
- `opik-mcp` reads `enabled: false` and omits `ask_ollie` and `run_experiment` from the advertised tool list at handshake time
- All 9 other tools (`read`, `list`, `score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `save_eval_item`) work normally against `opik-backend`

This is decided at MCP `initialize` time, so the host UI never even shows the tools that wouldn't work. Cleaner than failing at `tools/call` time.

## Failure modes worth documenting

| Failure | What user sees | What MCP does |
|---|---|---|
| `OPIK_API_KEY` missing/wrong | `401` from any upstream call | Surface as MCP error with `code: -32001` ("Auth failed — check `OPIK_API_KEY`") |
| `COMET_WORKSPACE` not in user's accessible workspaces | `403` from `validateWorkspaceAccess` | Surface as MCP error with workspace name in message |
| Pod cold-start > 120s | `notifications/tasks/updated` shows timeout | Cancel task, return error suggesting "try again — pod is warming" |
| Pod returns `confirm_required` event | Host shows `elicitation/create` prompt | Pause SSE consumption, wait for user response, forward back to pod |
| Host doesn't advertise `capabilities.elicitation` | `ask_ollie` works but no confirm prompts | Fall back to "auto-approve" mode with WARN log; tool description notes the limitation |

## What changes in Phase 2

For reference — this is what we're explicitly *not* doing now:

- `comet-backend` becomes an OAuth 2.1 Authorization Server (PKCE, DCR, CIMD verified hosts)
- `opik-mcp` becomes an OAuth resource server with RS256 JWT verification + JWKS rotation
- New `POST /oauth/mint-user-api-key` exchanges the JWT for a short-lived Opik API key (so the pod can call `opik-backend` as the user without holding long-lived secrets)
- Pod gets a JWT verifier (replaces `PPAUTH`) so the always-warm MCP service can forward a service-account JWT
- All sessions/events/quotas move to Redis
- `mcp_*` MySQL tables for audit trail
