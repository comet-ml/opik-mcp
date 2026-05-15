# Phase 1 — Local install, API-key auth

The first shippable version. Local Python process the user installs, authenticates with one API key, gets the full tool surface including `ask_ollie`. Cloud Comet users only (self-hosted users without `ollie-assist` get 9 tools, no `ask_ollie`/`run_experiment`).

---

## What's IN Phase 1

- All 11 tools (`read`, `list`, `ask_ollie`, `score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `run_experiment`, `save_eval_item`). No MCP Resources — reads are tools (ADR 0004 D1).
- MCP Streamable HTTP transport via `modelcontextprotocol/python-sdk`
- MCP Tasks primitive for `ask_ollie` / `run_experiment` (with blocking-SSE fallback)
- SSE event vocabulary translation (Ollie pod → MCP frames)
- Elicitation (`elicitation/create` for `confirm_required` events from the pod)
- Pod cold-start handling (poll `/health/ready` up to 2 min, hide behind Tasks)
- In-memory session/event state (single-user, single-process)
- Three host targets: **Claude Code, Cursor, VS Code Copilot**

## What's OUT (deferred to Phase 2)

- OAuth 2.1 AS / PKCE / DCR / CIMD in `comet-backend`
- RS256 JWT issuance + JWKS publishing + rotation
- Pod-side JWT verifier (W3) — pod stays cookie-based (`PPAUTH`)
- `/oauth/mint-user-api-key` — Phase 1 uses `OPIK_API_KEY` directly
- Hosted always-warm Deployment, HPA, mTLS, NetworkPolicy
- Redis (sessions, event log, mint cache, revocation bloom)
- Multi-tenant pods — Phase 1 is single-user-per-pod (orchestrator label includes username)
- Customer-facing admin UI / dashboard surface (also deferred from Phase 2 — see ADR 0006)
- Central audit log / `mcp_*` MySQL tables
- Central quotas
- **claude.ai support** (claude.ai doesn't run local MCP servers)
- CIMD verified-host pre-registration

## What requires a backend change

Exactly one tiny patch to `comet-backend`. The existing `GET /opik/ollie/compute` is cookie-only — local MCP servers have no cookie. PR adds a sibling endpoint `GET /opik/ollie/compute-api-key` that accepts the standard `Authorization` / `Comet-Sdk-Api` header (same pattern as `/opik/auth`, `/opik/workspace-permissions`). Cookie path unchanged.

**PR:** [comet-ml/comet-backend#5555](https://github.com/comet-ml/comet-backend/pull/5555)

Zero changes in `opik-backend`. Zero changes in `ollie-assist` (pod stays cookie-auth via `PPAUTH`).

---

## Week-1 vertical-slice PoC

Before writing the full surface, prove the hard path works. **One engineer-week.** Bail-out plan if any day's milestone fails.

### Day 1–2: Hello-world MCP server

- `pyproject.toml` + Python 3.13 + `mcp[cli]` + `fastapi` + `uvicorn`
- Single tool `hello()` returning `"hello"`
- Auth: hardcoded bearer `dev-token-123`, no JWT, no JWKS
- Run on `localhost:8080`
- Verify Claude Code and MCP Inspector both connect and see the tool
- **Bail-out condition:** if the Python SDK + Streamable HTTP transport doesn't work end-to-end in 2 days, escalate

### Day 3–5: `ask_ollie` against a real pod, no Tasks

- Hardcode one pod URL (your own, manually warm) — skip `/ollie/compute` entirely for now
- Hardcode `PPAUTH` cookie value
- Hardcode `X-Opik-User-API-Key` (skip the mint)
- Implement SSE event-vocabulary translation: Ollie's `thinking_delta` / `tool_call_*` / `message_end` → MCP `notifications/progress`
- **Blocking-SSE path only**, no Tasks primitive
- Send a real query, get a real response in Claude Code chat
- **This is the question.** If Ollie's SSE shape doesn't map cleanly onto `notifications/progress`, we find out here for ~$0.

### Day 6–8: Tasks primitive on top

- Same setup but advertise `capabilities.experimental.tasks` and return `CreateTaskResult` immediately
- Wire `notifications/tasks/updated` and `tasks/get`
- Test against Claude Code, Cursor, VS Code Copilot — this is where you discover which hosts actually support Tasks vs silently fall through

### End of week 1

Decision point:
- **PoC works** → commit to full Phase 1 build
- **PoC has integration issues** → fix or adapt before scaling
- **PoC fundamentally doesn't work** → escalate to product, reconsider scope

---

## Phase 1 full-build order (after PoC)

1. **Pod-discovery integration.** Replace hardcoded pod URL with `GET /opik/ollie/compute-api-key`. Parse `Set-Cookie` for `PPAUTH`. Implement `/health/ready` poll. **Depends on:** comet-backend PR landing.
2. **Read surface — `read` / `list` tools.** Entity registry covering 18 Opik entity types, adaptive compression (FULL / MEDIUM / SKELETON), name-lookup with disambiguation, `opik://` URI input parser. Backed by the typed `httpx` client.
3. **Tools — direct writes (7).** Each is a thin `httpx.AsyncClient.post()` to `opik-backend`. Generate the typed client from Opik's OpenAPI via `datamodel-code-generator`. Build in JTBD order: `score`, `comment` (annotate); `add_test_suite_items`, `save_prompt_version` (curate); `create_trace`, `create_span` (author); `save_eval_item` (iterate).
4. **`run_experiment` via Tasks.** Same pattern as `ask_ollie` but the pod call is different.
5. **`InitializeResult.instructions`.** Per-session system-prompt blob (workspace, Opik URL, today's date, default project, tool-selection guidance). Rendered at `initialize` time from `Settings`.
6. **Elicitation.** Round-trip `confirm_required` events through MCP `elicitation/create`. Gate on host `capabilities.elicitation`.
7. **Distribution.** `uvx opik-mcp` (PyPI). Per-host config snippets in `docs/install/`.
8. **Host conformance suite.** Automated tests against Claude Code, Cursor, VS Code Copilot, MCP Inspector using the SDK's vendored conformance fixtures.

---

## Open questions for Phase 1

- **One key or two?** Standardize on `OPIK_API_KEY` (used as the Comet API key too, since they're the same key DB in cloud) or separate `COMET_API_KEY` + `OPIK_API_KEY`? **Strong recommendation: one key.** Cleaner UX, matches the existing `extensions/cursor/src/mcp/mcpService.ts` pattern.
- **Self-hosted users without Anthropic key.** Do they get `ask_ollie` if they deploy their own `ollie-assist`? Yes — the local MCP server is host-agnostic, just point it at the right pod-discovery URL.
- **Workspace-switching UX.** Each host install is bound to one workspace via `COMET_WORKSPACE`. Switching = re-install or run a second instance. Is that acceptable?
- **First-API-key assumption.** `comet-backend` `provisionOlliePod()` seeds the pod with the user's *first* Comet API key as `OLLIE_USER_OPIK_API_KEY`. If the user has multiple, that may not match the key the MCP server is using. Recommend documenting "use one key everywhere."

See [`open-questions.md`](./open-questions.md) for the full list.
