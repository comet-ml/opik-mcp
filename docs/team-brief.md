# Ollie MCP — Team Brief

| | |
|---|---|
| **Status** | Draft — open for team review |
| **Last updated** | 2026-05-13 |
| **Jira** | OPIK-6439 — _Improve Opik MCP server and include Ollie tool_ |
| **Design doc** | [`design.md`](./design.md) — sequence diagrams, schemas, full task list |

> **How to read this:** start here for the shape, the choices, and the team-by-team build list. Jump to the design doc only when you need the wire-level detail.
>
> **Phase split:** Product asked for Phase 1 local-install (API-key) first, then hosted/OAuth in Phase 2. See [`phase-1.md`](./phase-1.md). This brief describes the full Phase-2 target; Phase 1 is a strict subset.

---

## TL;DR

- **What:** a hosted, OAuth-secured MCP server at `https://www.comet.com/api/v1/mcp` exposing Opik **and** Ollie to external AI hosts (Claude Code, Cursor, claude.ai, VS Code Copilot).
- **Surface:** **9 outcome-oriented tools** — one of them (`ask_ollie`) is the doorway into Ollie's full investigative agent; the other 8 are deterministic write tools. Reads are **12 URI-addressable resources**, not tools.
- **Why now:** Ollie is locked behind comet.com cookie auth — external hosts can't reach it. `opik-mcp` (TS) has 30+ generic tools and sits in the tool-count accuracy collapse zone (~14 % selection).
- **Why a new `ollie-mcp` repo:** per-user Ollie pods have no stable URL, cold-start up to 2 min (vs MCP host ~30 s timeout), and no JWT verifier. Wrong tier to host the public endpoint on.
- **What's NOT changing:** `opik-mcp` (TS) stays as the scripted / CI / no-LLM path. `opik-backend` REST is unchanged. Ollie's session model is unchanged.
- **Ship model:** single GA, no v1/v2 staging. W1–W5 critical path, W6–W10 in parallel.

---

## Table of contents

1. [The problem](#the-problem)
2. [Why a separate `ollie-mcp` repo](#why-this-is-a-separate-ollie-mcp-repo)
3. [Why Python (not TypeScript)](#why-python-not-typescript)
4. [The stack](#the-stack)
5. [The nine tools](#the-nine-tools)
6. [Reads → resources](#reads--resources)
7. [Cold start, MCP Tasks, SSE proxy](#cold-start-mcp-tasks-and-the-sse-proxy)
8. [Connection flow](#connection-flow)
9. [API surface](#api-surface)
10. [Workspace and project selection](#workspace-and-project-selection)
11. [User flows](#user-flows)
12. [What's new vs reused](#whats-new-vs-what-were-reusing)
13. [Self-hosted](#what-ships-in-self-hosted)
14. [Quotas, cost, discovery](#quotas-cost-and-discovery)
15. [Workstreams](#workstreams-parallel-build-single-ga)
16. [Host matrix](#host-matrix-verified-at-each-release)
17. [Required work by team](#required-work)
18. [Open questions](#open-questions)

---

## Glossary

| Term | Meaning |
|---|---|
| **MCP** | Model Context Protocol — wire protocol for AI hosts to call tools / read resources. Spec rev `2025-11-25`. |
| **MCP host** | The user-facing AI app (Claude Code, Cursor, claude.ai, VS Code Copilot). |
| **MCP Tasks** | Experimental MCP primitive for long-running tool calls — server returns `CreateTaskResult` immediately, host polls for completion. |
| **Streamable HTTP** | MCP transport: POST for requests, GET for server→host SSE. |
| **Elicitation** | MCP server→host prompt asking the user to confirm something before the tool proceeds. |
| **OAuth 2.1 / PKCE** | Authorization-code flow with proof-key — standard for human-initiated MCP auth. |
| **DCR** (RFC 7591) | Dynamic Client Registration — hosts register their `client_id` automatically. |
| **CIMD** | Client Identity Metadata Document — verified hosts skip DCR by publishing a metadata URL as their `client_id`. |
| **JWKS** | JSON Web Key Set — public keys for verifying JWT signatures, served at `/.well-known/jwks.json`. |
| **JTBD** | Jobs-to-be-done — the lens used to pick the 9 tools. |
| **WORM** | Write-Once-Read-Many storage (S3 Object Lock) for audit-log archive. |
| **Pod / per-user pod** | An `ollie-assist` instance, one per workspace, scaled from zero by codepanels. |

---

## The problem

External AI hosts can't reach Ollie today: Ollie is locked behind Comet's React UI cookie auth, with no machine-callable surface. Even if they could, each Ollie instance is a **per-user pod** that scales from zero — the FE asks `comet-backend GET /api/opik/ollie/compute` to discover the user's pod URL, then polls `/health/ready` for up to two minutes during cold start; external hosts have no way to do that discovery dance.

The existing `opik-mcp` (TS) sits in the tool-count accuracy-collapse zone: 30+ generic CRUD tools, with selection accuracy dropping from ~43% to ~14% as tool count grows. Self-hosted users want the same workflow but today only have `opik-mcp` (no LLM, generic surface).

**What we want:** one stable, always-warm MCP server in front of the per-user pod farm and the Opik REST API, with OAuth, audit logging, the right tool granularity, and a self-hosted bundle. (The customer-facing admin / dashboard surface that originally accompanied this is deferred — see ADR 0006.)

---

## Why this is a separate `ollie-mcp` repo

Three things make it untenable to host the MCP endpoint inside `ollie-assist` itself.

First, **per-user pods have no stable external URL.** `ollie-assist` runs one pod per workspace, scaled from zero by an external orchestrator (helm install/uninstall on first request, idle TTL). External MCP hosts (Claude Code, Cursor) register one URL in their config and can't do per-call workspace→pod discovery.

Second, **cold start is up to two minutes.** MCP hosts time out on `tools/call` after ~30 s. We need an always-warm service that returns a `CreateTaskResult` (MCP Tasks primitive) within 2 s — before the pod is even awake — and narrates warmup via progress notifications.

Third, **the per-user pod has no OAuth and no JWT verifier.** Today it trusts a `BROWSER_AUTH` cookie value injected at pod creation that matches the user's Comet session cookie. Putting OAuth there means every per-user pod re-implements a JWT verifier with key rotation. Wrong tier.

The new server therefore lives in its own repo with a dedicated Docker image, deployed always-warm (≥2 replicas) at the comet gateway tier. It calls `comet-backend` for OAuth, `/api/opik/ollie/compute` (pod discovery), `/oauth/mint-user-api-key` (per-session user-key mint), and the short-lived RS256 service-account JWT the pod's nginx validates; it calls the user's per-user `ollie-assist` pod for `ask_ollie` and `run_experiment` via the pod's existing `/sessions` API; and it calls `opik-backend` directly for the nine REST-backed tools (`read`, `list`, `score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `save_eval_item`), with no pod involvement.

**Role of each repo.** `ollie-mcp` (new) is the hosted MCP server — always-warm, multi-replica, public-facing. `ollie-assist` remains the per-user pod, scale-from-zero, with its `/sessions` API unchanged and a new pod-side JWT verifier added. `comet-backend` hosts the OAuth AS, pod discovery, user-key mint, service-account JWT issuer, and JWKS publisher. `opik-backend` is unchanged — it's the REST upstream for direct-write tools and resources. `opik-frontend` has no MCP-specific changes at launch — the customer-facing "MCP" workspace settings tab is deferred per ADR 0006 (an in-Opik "Connect" install banner may still ship as small, separate install UX). `opik-mcp` (TS, existing) stays where it is and gets polished as the scripted / CI / no-LLM path.

---

## Why Python (not TypeScript)

This is the question that comes up first, because `opik-mcp` is already TypeScript and reusing that stack would feel like the obvious move. The deciding factor isn't HTTP throughput — the `ollie-mcp` workload is I/O-bound (downstream fan-out to `opik-backend` and SSE proxying from the pod), and either runtime handles thousands of concurrent SSE connections comfortably on commodity nodes. The deciding factor is **code share with `ollie-assist`**.

`ollie-mcp`'s central job is translating Ollie's pod-side SSE event vocabulary (`thinking_delta`, `tool_call_start`, `tool_call_delta`, `confirm_required`, `navigate`, `compaction_*`, `message_end`) into MCP frames (`notifications/progress`, `notifications/tasks/updated`, `elicitation/create`). That vocabulary lives in `ollie-assist`'s `src/ollie_assist/types/sse.py`. In Python the translator is one import — `from ollie_assist.types.sse import SessionEvent, ThinkingDelta, ToolCallStart, ConfirmRequired, Navigate` — and event-shape drift becomes a CI failure, not a runtime bug. In any other language every change to Ollie's emitter forces a hand-translation in two repos, and the kinds of bugs that show up are the worst kind: silent, async, host-specific. The same logic applies to the auth context types (`WorkspaceContext`, multi-tenant key handling in W3) and to the `httpx.AsyncClient` factory that talks to `opik-backend` — `ollie-mcp` reuses the exact same factory `ollie-assist` already uses for `get_or_create_user_opik_client(workspace)`.

The same argument cuts the other direction for `opik-mcp` (TS): it's a self-contained scripted/CI tool with no shared event vocabulary, no per-user pod, no SSE translation, and a thin REST wrapper — TypeScript is the right call there and we keep it.

**Second-order arguments** line up the same way: one on-call language reduces context-switching for the team that owns Ollie; the same hire pool covers both services; the `opik` Python SDK is a pinned dependency in both repos so version bumps are one PR per repo, not three; the first-party `modelcontextprotocol/python-sdk` ships Streamable HTTP, OAuth resource-server helpers, and the experimental Tasks primitive out of the box. Python's tradeoffs (≈120 MB RSS per replica vs ≈30 MB for a Go binary; ~700 ms cold start) are irrelevant for an always-warm Deployment with a multi-replica HPA. Full per-language tradeoff (including Go / Rust runner-ups): design doc §2.2.2.

---

## The stack

```
                                  ┌───────────────────────────────────────┐
External MCP host ──MCP HTTP─────▶│  ollie-mcp  (NEW repo, NEW image)     │
(Claude Code, Cursor,             │  Python 3.13, FastAPI, sse-starlette  │
 claude.ai, VS Code Copilot)      │  Always warm, ≥2 replicas, Redis      │
                                  │  ─────────────────────────────────── │
                                  │  Public:                              │
                                  │   /api/v1/mcp                         │
                                  │   /.well-known/oauth-protected-...    │
                                  │  Inside:                              │
                                  │   RS256/JWKS verifier                 │
                                  │   Redis session map + SSE event log   │
                                  │   MCP Tasks engine                    │
                                  │   Tool dispatcher (Ollie vs REST)     │
                                  │   Per-session user-key cache          │
                                  └────────┬──────────┬───────────────────┘
                                           │          │
                  ┌────────────────────────┘          └────────────────────────┐
                  │                                                            │
                  ▼                                                            ▼
        ┌──────────────────┐                                       ┌────────────────────┐
        │ comet-backend    │                                       │ opik-backend       │
        │ ──────────────── │                                       │ ────────────────── │
        │ OAuth 2.1 AS     │                                       │ REST endpoints     │
        │  + PKCE + DCR    │                                       │ for the 7 direct   │
        │  + CIMD          │                                       │ write tools + all  │
        │ JWKS publisher   │                                       │ resource reads.    │
        │ /ollie/compute   │                                       │ (Cluster-internal) │
        │ /mint-user-api-  │                                       └────────────────────┘
        │   key            │
        │ Service-account  │
        │   JWT issuer     │
        └────────┬─────────┘
                 │ provisions
                 ▼
        ┌──────────────────────┐         ┌──────────────────────────┐
        │ codepanels           │ ──────▶ │ ollie-assist pod         │
        │ orchestrator         │         │ (one per workspace)      │
        │ (existing)           │         │ ──────────────────────── │
        └──────────────────────┘         │ /sessions API + SSE      │
                                         │ + NEW: pod-side JWT      │
                                         │   verifier (JWKS)        │
                                         └──────────────────────────┘
```

### Pieces by repo / language / framework

| Repo | Role | Language / Framework |
|---|---|---|
| `ollie-mcp` (NEW) | MCP server: Streamable HTTP, sessions, Tasks | Python 3.13, FastAPI, `sse-starlette`, `mcp` SDK (`modelcontextprotocol/python-sdk` ^1.12) |
| `ollie-assist` | Per-user pod: agent, sub-agents, skills, jq | Python 3.13, FastAPI, Anthropic SDK |
| `ollie-assist` (nginx) | Pod-side JWT verifier | Lua / nginx (`lua-resty-openidc`) |
| `comet-backend` | OAuth 2.1 AS, CIMD, JWKS, service-account JWT issuer | Java, Dropwizard |
| `opik-backend` | REST upstream for direct-write tools + resources | Java, Dropwizard, JDBI |
| `opik-frontend` | No MCP changes at launch — customer-facing admin tab deferred (ADR 0006) | TypeScript, React |
| `opik-mcp` (existing) | Scripted / CI / no-LLM path | TypeScript, `@modelcontextprotocol/sdk` |

### Anchor libraries — don't hand-roll what's solved

- **`mcp` SDK** — Streamable HTTP transport, OAuth resource-server validation (`TokenVerifier` / `AuthSettings`), experimental Tasks engine (`server.experimental.enable_tasks()`).
- **`joserfc`** — JWT/JWKS with multi-`kid` rotation.
- **`httpx-sse`** — consumes upstream pod SSE.
- **`redis-py` (async)** — sessions, event log, warm-up coordinator, JWKS pub-sub invalidation.
- **`datamodel-code-generator`** — builds the Opik typed client from OpenAPI at CI time.
- **`sse-starlette`** — serves the blocking-SSE fallback.
- **MCP Inspector** (`make inspect`) — manual smoke; SDK's vendored conformance fixtures gate CI.

Full pinned-version table: design doc §2.2.2.

---

## The eleven tools

Three sources of truth shape this surface. First, **JTBD buckets** — Read, Investigate, Curate, Iterate, Annotate, Author. Second, the **Opik 2.0 surface**: test suites (`type=evaluation_suite`) are the **only** evaluation entity in 2.0. The legacy generic-dataset concept has been retired; what older docs called a "dataset" is a test suite. All eval-row writes and item curation target test suites. Third, **Python SDK naming**: `client.trace()` / `client.span()` are the SDK methods; the `log_*` prefix is feedback-scoring only, so the tool name is `create_trace` (not `log_trace`).

### At a glance

| # | Tool | Bucket | Dispatch | Notes |
|---|---|---|---|---|
| 1 | `read` | Read | `opik-backend` REST | Universal entity read; accepts UUID, name, or `opik://` URI |
| 2 | `list` | Read | `opik-backend` REST | Paginated list (`page`/`size`); pipe-delimited table |
| 3 | `ask_ollie` | Investigate | Ollie pod | Only LLM-gated tool; daily quota; uses Tasks primitive |
| 4 | `score` | Annotate | `opik-backend` REST | High-frequency annotation |
| 5 | `comment` | Annotate | `opik-backend` REST | "Sticky-note" tool |
| 6 | `add_test_suite_items` | Curate | `opik-backend` REST | Bulk; `source.kind=traces` seeds from telemetry |
| 7 | `save_prompt_version` | Curate | `opik-backend` REST | Polymorphic: create or version via optional `prompt_id` |
| 8 | `create_trace` | Author | `opik-backend` REST | Accepts inline `spans[]`; renamed from `log_trace` |
| 9 | `create_span` | Author | `opik-backend` REST | Append to existing trace by `trace_id` |
| 10 | `run_experiment` | Iterate | Ollie pod | Always Tasks primitive; final result carries `experiment_id` |
| 11 | `save_eval_item` | Iterate | `opik-backend` REST | Single-row counterpart to `add_test_suite_items` |

### Details

1. **`ask_ollie`** _(Investigate)_ — signature `(query, page_context?, attach_resources?, thread_id?, continuation_token?)`, dispatched to the user's Ollie pod. Long-running; uses the Tasks primitive when the host supports it, blocking-SSE with progress notifications otherwise. The only LLM-gated tool, and the only one with a per-day quota.

2. **`score`** _(Annotate)_ — signature `(target, name, value, reason?)`, dispatched to `opik-backend` REST. High-frequency annotation: "set hallucination=0.7 on trace abc-123."

3. **`comment`** _(Annotate)_ — signature `(target, text)`, dispatched to `opik-backend` REST. The "add a sticky-note" tool.

4. **`add_test_suite_items`** _(Curate)_ — signature `(test_suite_id, items, source?)`, dispatched to `opik-backend` REST `POST /v1/private/test-suites/{id}/items`. Bulk-curate rows into a test suite; `source.kind=traces` seeds them from telemetry.

5. **`save_prompt_version`** _(Curate)_ — signature `(prompt_id?, name?, template, metadata?)`, dispatched to `opik-backend` REST `POST /v1/private/prompts/versions`. Creates a brand-new prompt (omit `prompt_id`) or adds a new version to an existing one. Server-side incremental versioning.

6. **`create_trace`** _(Author)_ — signature `(trace)`, dispatched to `opik-backend` REST `POST /v1/private/traces`. Programmatic ingestion for CI / scripted callers. Accepts an inline `spans` array for "one trace plus N spans" in a single call. Renamed from `log_trace` to match `client.trace()` in the SDK.

7. **`create_span`** _(Author)_ — signature `(trace_id, span)`, dispatched to `opik-backend` REST `POST /v1/private/spans`. The append-to-existing-trace path: a host that already has a `trace_id` attaches a span without re-sending the whole trace. Matches `client.span()` in the SDK.

8. **`run_experiment`** _(Iterate)_ — signature `(experiment_config)`, dispatched to the Ollie pod. Long-running. Always uses the Tasks primitive — the server returns `CreateTaskResult` before the pod is even warm. Final result carries `experiment_id`; the host can deep-link by calling `read("experiment", <id>)`.

9. **`save_eval_item`** _(Iterate)_ — signature `(target, expected, metadata?)`, dispatched to `opik-backend` REST `POST /v1/private/test-suites/{id}/items`. The inline single-row counterpart to `add_test_suite_items`: turn one trace/span/thread into one test-suite row with an `expected` value.

### Why each exists

`ask_ollie` is the only LLM-gated tool because investigation is unbounded — it needs Ollie's planner, sub-agents, skills, and jq chains. Exposing those building blocks separately would push the host into 30+-tool accuracy collapse.

`score` and `comment` are high-frequency annotation actions. "Add a comment to trace abc-123" via `ask_ollie` is a 5–15 s LLM hop; direct it's ~200 ms. Worth the tool slots.

`add_test_suite_items` and `save_eval_item` both write to test suites but differ in shape: bulk-with-`source` vs single-with-`target`. One polymorphic tool would confuse the host LLM more often than two clearly-named tools do. (This is open question #1 — current proposal is keep two.)

`save_prompt_version` is a single tool that handles both "create" and "version" via the optional `prompt_id`. Prompts are versioned in Opik, so the surface is naturally polymorphic.

`create_trace` is the programmatic-host story for CI and batch jobs — a direct map onto the existing REST endpoint, accepting an inline `spans` array for the common "trace + nested spans in one call" shape. `create_span` covers the second flow: append a span to a trace that already exists, without re-sending the rest. Two tools, two REST endpoints, no dispatcher polymorphism.

**Why there is no `create_thread` tool.** A thread in Opik is not a standalone entity — it's the `thread_id` field on a trace, and the thread surfaces (annotation, scoring, listing) derive from grouping traces by that field. There is no `POST /v1/private/threads` to wrap. Hosts start a new thread by calling `create_trace` with a fresh `thread_id`, and extend it by reusing the same `thread_id` on subsequent traces. The read and annotate surfaces for threads still work — `score` and `comment` accept `target.type = "thread"` — but the _create_ primitive doesn't exist, so the tool list doesn't fake one.

`run_experiment` exists because evals take minutes and the Tasks primitive is the only honest way to expose minutes-long work over MCP.

### Reads → `read` / `list` tools

Reads are tools, not MCP resources (ADR 0004 D1). Two universal tools cover the entire read surface:

- **`read(entity_type, id, max_tokens?)`** — fetch one entity. `entity_type` ∈ {trace, span, project, thread, dataset, dataset_item, experiment, experiment_item, prompt, prompt_version, test_suite, feedback_definition, automation_rule_evaluator, annotation_queue, alert, dashboard, optimization, workspace_configuration}. `id` accepts a UUID, a name (for nameable types — disambiguates if multiple match), or an `opik://<entity_type>/<id>` URI. Composite entities (trace, prompt) inline child collections (spans, versions). Output is a `[read: …]` header plus JSON, adaptively compressed (FULL / MEDIUM / SKELETON) based on a `max_tokens` budget.
- **`list(entity_type, name?, page?, size?)`** — paginate a collection. Listable types: project, dataset, experiment, prompt, feedback_definition, automation_rule_evaluator, annotation_queue, dashboard, optimization, test_suite. Output is a pipe-delimited table with a pagination footer when `total > page * size`.

Both tools accept `opik://<entity_type>/<id>` URIs as the `id` parameter for forward-compat with any future resource-aware client. **Why tools, not resources:** Claude Code does not surface MCP resources to the LLM, and Cursor only renders them as `@`-mention completions. With ~95% of mid-session usage being reads, putting them behind a host-invisible surface defeats the purpose. See ADR 0004 §"Empirical inputs §4" for the client-support matrix.

---

## Cold start, MCP Tasks, and the SSE proxy

The single hardest UX problem: when a user first calls `ask_ollie`, their per-user pod may not exist yet. Helm install + image pull + warmup can take up to two minutes. MCP hosts time out at ~30 s. The solution is the **MCP Tasks primitive** (2025-11-25 spec), with a graceful blocking-SSE fallback for hosts that don't yet support Tasks.

### Sequence (Tasks-capable host)

```
1. Host  → tools/call { name:"ask_ollie", arguments:{...},
                        _meta: { task: { ttl: 600000 } } }
2. Server →  /ollie/compute (with user identity) → returns computeURL
3. Server →  CreateTaskResult { task: { taskId, status:"working",
              statusMessage:"Starting Ollie...", createdAt:"<ISO>" },
              _meta: { "io.modelcontextprotocol/model-immediate-response":
                       "Ollie is starting — this can take up to 2 minutes on first use." } }
   (returned in <2 s, before pod is awake)
4. Host LLM tells user "Ollie is starting..."
5. Server (async): polls pod's /health/ready (1 s interval)
6. Server → notifications/tasks/updated { task: { status:"working",
                       statusMessage: "Pod ready, calling agent..." } }
7. Server →  pod /sessions  (creates session, opens SSE stream with service-account JWT)
8. Server ← pod SSE events (thinking, tool_use, ...)
9. Server → notifications/tasks/updated (every meaningful event)
10. On pod final, server marks the task `status:"completed"` with the CallToolResult
    on the Task object; host retrieves via `tasks/get`.
```

### Fallback (host doesn't advertise Tasks support)

The server holds the `tools/call` HTTP response open as SSE, emits `notifications/progress` frames every 10 s during warmup ("Starting Ollie", "Pod ready", "Reading traces", ...), and resolves with `CallToolResult` when done. Identical producer code path; only the response shape differs.

Capability negotiation happens at `initialize`: the server advertises **`capabilities.experimental.tasks`** (the spec is still experimental in 2025-11-25); the host opts into the Tasks shape per-call by setting `_meta.task.ttl` on `tools/call`. Without that meta — or when the host's `initialize` response doesn't advertise `capabilities.experimental.tasks` — the same request resolves through the blocking-SSE path. Elicitation is gated on host `capabilities.elicitation`. Cancellation is **in-band** via the `notifications/cancelled` JSON-RPC notification on the main `/api/v1/mcp` endpoint, per MCP spec — there is no separate `/cancel` HTTP route.

### Resumability

`notifications/progress` and SSE events both carry monotonic `seq` IDs. On disconnect, the host reconnects with `Last-Event-ID: <seq>`; the server replays missed events from Redis. The Redis event log key is `(session_id, event_seq) → event` with TTL = pod idle TTL (30 min). Failover across replicas is transparent.

---

## Connection flow

What happens, end-to-end, when a user types `/connect mcp` in Cursor.

1. **Discovery.** Host hits `https://www.comet.com/.well-known/oauth-protected-resource` (RFC 9728). Returns the authorization server URL. Host then fetches `https://www.comet.com/.well-known/oauth-authorization-server` (RFC 8414) for AS metadata: authorize / token / revoke endpoints, supported scopes, DCR endpoint.

2. **Client registration.** Verified hosts (Anthropic claude.ai + Claude Code, Cursor, Microsoft VS Code Copilot at launch) skip DCR via CIMD — their `client_id` IS their metadata URL, e.g. `client_id=https://cursor.com/.well-known/mcp-client.json`. The AS fetches that URL and validates request signatures against the published JWKS. Other hosts use Dynamic Client Registration (RFC 7591): POST to `/oauth/register` returns a fresh `client_id`.

3. **Authorize.** Host opens the user's browser to `/oauth/authorize?response_type=code&client_id=...&code_challenge=...&scope=mcp:read+mcp:write:traces+mcp:write:test_suites+mcp:write:annotations+mcp:write:experiments+mcp:write:prompts+mcp:ask_ollie+mcp:run_experiment&resource=https://www.comet.com/api/v1/mcp`. If the comet.com session cookie is present, the consent screen renders immediately; otherwise the user logs in first.

4. **Consent.** User picks the workspace, approves scopes. Granular scopes mean the consent screen shows exactly which tool families the host requested. The consent screen is the only user-level scope control at launch — per-workspace admin scope disables are deferred (ADR 0006).

5. **Token exchange.** Browser redirects to the host's loopback `redirect_uri` with `code=...`. Host POSTs to `/oauth/token` with the code + PKCE `code_verifier`. AS returns an **RS256-signed JWT** carrying `sub`, `workspace`, `scope`, `aud=https://www.comet.com/api/v1/mcp`, `exp`. JWT signing key is published at `/.well-known/jwks.json`.

6. **First MCP call.** Host opens `POST /api/v1/mcp` with `Authorization: Bearer <jwt>`. Server validates locally via JWKS (cached in-process for 1 h, refreshed on `kid` miss; emergency rotation pushes a Redis pub/sub invalidate), allocates a 192-bit-entropy `Mcp-Session-Id` bound to the JWT's `(sub, workspace_id, jti)`, and returns it in the response header. Subsequent calls echo the header.

7. **Per-session user-key mint.** First time the server sees a session, it calls comet-backend `POST /oauth/mint-user-api-key` (internal, mTLS-only) and gets back an `X-Opik-User-API-Key` scoped to the JWT's user + workspace. Cached in Redis for the session lifetime.

8. **Per-call user-key forwarding.** Every call to `opik-backend` or the per-user pod carries `X-Opik-User-API-Key` + `Comet-Workspace`. The pod additionally carries a short-lived service-account JWT.

End-to-end ~10 s, one consent click. After this, the host stores the access token and never prompts again until expiry.

---

## API surface

### HTTP endpoints exposed by `ollie-mcp`

`POST /api/v1/mcp` is the MCP Streamable HTTP entrypoint and receives JSON-RPC frames (including `notifications/cancelled` for in-band task cancellation). `GET /api/v1/mcp` is the long-poll SSE for server→host notifications between requests and supports `Last-Event-ID` resumption. `DELETE /api/v1/mcp` is explicit session teardown via the `Mcp-Session-Id` header. `GET /health` / `/healthz` / `/ready` are standard liveness/readiness probes, and `GET /metrics` is Prometheus exposition.

OAuth endpoints live on `comet-backend`: `/oauth/authorize`, `/oauth/token`, `/oauth/register`, `/oauth/revoke`, plus the three `/.well-known/*` documents (`oauth-protected-resource`, `oauth-authorization-server`, `jwks.json`).

### MCP methods implemented

`initialize` returns capabilities `tools`, `prompts`, `experimental.tasks`, and `elicitation` — **no `resources` capability** (ADR 0004 D1). The handshake also populates `InitializeResult.instructions` with a per-session blob covering workspace, Opik URL, default project, and tool-selection guidance (ADR 0004 D6). `tools/list` returns 11 tools and applies token-scope filtering (tools whose scope the token does not carry are omitted; per-workspace admin scope disables are deferred per ADR 0006). `tools/call` routes to the pod (`ask_ollie`, `run_experiment`) or to `opik-backend` (everything else, including `read` / `list`). `prompts/list` and `prompts/get` expose a small set of canned starter prompts ("today's failures", "this week's prompt changes"). `tasks/get` and `tasks/cancel` implement the MCP Tasks primitive — state is stored in Redis and the terminal `CallToolResult` lives on the Task object. The server emits `notifications/progress` during long-running tools on the blocking-SSE path, `notifications/tasks/updated` for status transitions on the Tasks path, and `elicitation/create` whenever the pod's `confirm_required` events need user approval (gated on host `capabilities.elicitation`).

### Auth headers downstream

From host to `ollie-mcp`: `Authorization: Bearer <user JWT (RS256)>` plus `Mcp-Session-Id`. From `ollie-mcp` to `comet-backend`: mTLS plus `Authorization: Bearer <service JWT>`. From `ollie-mcp` to `opik-backend`: `X-Opik-User-API-Key: <minted>` plus `Comet-Workspace: <ws>`. From `ollie-mcp` to the pod's `/sessions`: `Authorization: Bearer <service JWT, RS256, aud=pod>` plus `Comet-Workspace: <ws>` plus `X-Opik-User-API-Key: <minted>`. From the pod back to `opik-backend` for state-sync: unchanged from today — `Authorization: <user_opik_api_key>` plus `Comet-Workspace`.

All tools use HTTP REST. There is **no** shelling-out to the `opik` CLI from `ollie-mcp` — every call is in-process `httpx`.

---

## Workspace and project selection

**Workspace = token-scoped.** Each OAuth token is bound to exactly one workspace, chosen at consent time. The host stores one connector per workspace (Claude Code and Cursor both support this natively). Switching workspaces means re-running OAuth or having a second connector. This removes a `workspace` parameter from every tool.

**Project = explicit per call.** Tools that need a project take it as a parameter (`create_trace.project_name`, `list.project_id` for project-scoped lists); tools that don't (`comment`, `score`, `read`) resolve it from the target trace/span ID. `ask_ollie` picks up project context from `page_context` (when the user has an Opik page open in their host) or from `attach_resources` (pre-fetched `opik://` URIs the host has on hand).

The consent screen at launch shows the workspace name and granular scope toggles — Read traces, spans, test suites, prompts, experiments (`mcp:read`); Write traces (`mcp:write:traces`); Write annotations (`mcp:write:annotations`); Write test-suite items (`mcp:write:test_suites`); Write prompts (`mcp:write:prompts`); Run experiments (`mcp:write:experiments`, `mcp:run_experiment`); Ask Ollie (`mcp:ask_ollie` — quota applies). Scope strings display verbatim — they are the stable wire contract that any future admin UI (deferred per ADR 0006) will mirror.

---

## User flows

### Flow 1 — Investigate → save → iterate (the hero flow)

```
User in Cursor: "look at my prod traces from today, anything failing?"
  └─ Cursor calls ask_ollie("recent failures in project foo")
     └─ ollie-mcp returns CreateTaskResult immediately
     └─ Pod warms up if cold (≤ 2 min); progress narrated via notifications/tasks/updated
     └─ Pod streams thinking → ollie-mcp → Cursor chat
     └─ Task transitions to completed; CallToolResult fetched via tasks/get:
        3-bullet summary + 5 trace IDs

User: "show me the worst one."
  └─ Cursor already has the IDs — calls read("trace", <id>)  (direct tool, no LLM hop)

User: "save this as a regression test."
  └─ Cursor calls save_eval_item(target={type:"trace", id:trace_id}, expected={...})
     └─ ollie-mcp → elicitation/create: "Save trace abc-123 to test suite X?" → user approves
     └─ ollie-mcp → opik-backend POST /v1/private/test-suites/{id}/items
     └─ Returns item ID

User: "now re-run my eval suite."
  └─ Cursor calls run_experiment(...)
     └─ Tasks primitive: CreateTaskResult immediately
     └─ Eval runs; task transitions to completed; tasks/get returns
        experiment_id (host can then call read("experiment", <id>) to deep-link)

4 MCP calls. Zero user-visible Ollie sessions. Cold-start latency hidden behind Tasks.
```

### Flow 2 — Pure annotate (no Ollie hop, no pod involvement)

```
User on a trace in their host: "comment here saying 'auth subagent is wrong'"
  └─ Host calls comment(target={trace:abc-123}, text="auth subagent is wrong")
     └─ ollie-mcp → elicitation/create: "Add comment to trace abc-123?" → approve
     └─ ollie-mcp → opik-backend POST /v1/private/traces/{id}/comments
     └─ Returns comment ID

1 MCP call. No LLM hop. No pod cold-start. ~200 ms round-trip.
```

### Flow 3 — Programmatic ingestion (CI / scripted)

```
CI script using an MCP client (no human in the loop):
  ├─ ollie-mcp authenticates via service-account OAuth client
  ├─ create_trace(trace=...) repeatedly  → opik-backend
  ├─ add_test_suite_items(test_suite_id="...", items=[...])
  └─ save_eval_item(target=..., expected=...) → test suite

User declines mcp:ask_ollie at consent:
  └─ ollie-mcp omits ask_ollie from tools/list   (host planner doesn't see a tool it can't call)
```

**Why `mcp:ask_ollie` is a separate scope:** compliance-sensitive customers (finance, healthcare) don't want telemetry summarized by an LLM. They want the deterministic tools without the agentic one. At launch, the lever is per-user (decline the scope at the OAuth consent screen); a workspace-admin force-disable is deferred per ADR 0006.

**Why we omit it from `tools/list` instead of 403-on-call:** host LLMs treat tools in `tools/list` as available — they'll plan around them, fail, retry, loop. Omitting prevents the loop.

---

## What's NEW vs what we're reusing

| New | Reused |
|---|---|
| `ollie-mcp` repo + image (Streamable HTTP, Tasks, session map, SSE event log, tool dispatcher) | `opik-backend` REST endpoints — thin wrappers behind the `read`/`list` tools and direct-write tools |
| Pod-side JWT verifier (nginx + JWKS) in `ollie-assist` | Ollie's session model (`POST /sessions`, SSE stream, `/confirm`) — what `ask_ollie` wraps |
| OAuth 2.1 AS + PKCE + DCR + CIMD in `comet-backend` | The sub-agent / skill / jq / tool-use loop inside the pod (unchanged) |
| RS256 service-account JWT issuer + JWKS publisher in `comet-backend` | `RemoteAuthService`'s cookie path — lets OAuth `/authorize` skip a second login |
| `/oauth/mint-user-api-key` (mTLS-only) in `comet-backend` | codepanels orchestrator — wakes pods on first `/ollie/compute` call |
| `/api/opik/ollie/compute` extension for service-account callers | Comet's OIDC + cookie session — reused by OAuth `/authorize` |
| Granular scopes (`mcp:read`, `mcp:write:*`, `mcp:ask_ollie`, `mcp:run_experiment`) in `comet-backend`'s `TokenService` | |
| MySQL tables: `mcp_audit_log`, `mcp_mint_audit`, OAuth control-plane tables | |
| `create_trace`, `create_span`, `save_eval_item` tool implementations | |

---

## What ships in self-hosted

Self-hosted Opik (the Docker-compose / Helm bundle) today includes `opik-backend`, `opik-frontend`, ClickHouse, MySQL — **not Ollie**.

At launch, `ollie-mcp` ships as part of the bundle and runs always-warm; direct-write tools, resources, and OAuth all work out of the box. `ollie-assist` ships in the bundle **conditionally** — the operator opts in by providing an Anthropic API key. The Anthropic SDK in `ollie-assist/src/ollie_assist/app.py:49` is unconditional, so without a key the bundle skips the Ollie image entirely and `ollie-mcp` omits `ask_ollie` and `run_experiment` from `tools/list`.

The self-hosted bundle uses `ollie-mcp`'s **API-key auth path** by default (no OAuth AS to stand up). Operators can optionally wire OAuth to their own IdP via the same routes. Pod provisioning in self-hosted goes through a thin helm-based provisioner that mirrors the codepanels orchestrator — for small deployments this can be a single static pod shared across the whole installation.

By default, the self-hosted `ask_ollie` daily quota is **0** (disabled). The operator opts in by setting `DAILY_QUOTA_ASK_OLLIE` to a positive value. Rationale: a fresh self-hosted install shouldn't silently start spending on Anthropic; the operator has to make that cost choice explicitly.

---

## Quotas, cost, and discovery

Quotas apply only to the tools with marginal LLM or compute cost. Direct writes are cheap REST and don't meter.

**Free (cloud):** 20 `ask_ollie` calls/day, 5 `run_experiment`/day, unlimited direct writes. Hard cap; 429 with `Retry-After` and an upgrade URL; the 80% soft-warning surfaces inline via `metadata.quota_warning` on each successful call (host UI can render it). **Pro (cloud):** 500 / 50 / unlimited. Soft cap; overage emailed and surfaced in billing UI. **Enterprise (cloud):** contractual on `ask_ollie` and `run_experiment`, unlimited direct writes. Per-workspace usage is visible to SRE via internal Grafana; a customer-facing usage view is deferred per ADR 0006. **Self-hosted:** operator's choice on both; both default to 0 so a fresh install doesn't silently start spending on Anthropic.

Operational caps independent of tier: 100 req/min per token for DoS protection, 10 MB response cap. Every `tools/call` that succeeds returns `metadata.quota_warning` once usage hits 80% of the cap, so hosts can surface a "you're approaching the limit" hint without polling.

**Discovery** at launch comes from four surfaces: an in-Opik "Connect to MCP" banner on the workspace page (one-click deeplink to Cursor / Claude Code / claude.ai); a comet.com docs section with the OAuth flow walkthrough; listings in claude.ai's MCP catalogue, Cursor's directory, and the `modelcontextprotocol.io` server registry — verified-connector badges via CIMD.

---

## Workstreams (parallel build, single GA)

There are no release-gate phases — everything ships at GA. The 9 workstreams below are parallelization seams. **W1–W5 are the GA-gating critical path; W6–W9 can land in parallel and can slip a week without delaying GA.** (The previously specified W6 "Admin & audit UI" workstream is removed per ADR 0006; subsequent workstreams renumbered.)

| WS | Name | Owner repo | Critical path | Summary |
|---|---|---|---|---|
| **W1** | `ollie-mcp` core | `ollie-mcp` | ✅ | New repo + image, FastAPI app, Streamable HTTP, session map, SSE event log, tool dispatcher, Tasks engine |
| **W2** | OAuth AS + JWKS | `comet-backend` | ✅ | `/oauth/*`, `/.well-known/*`, JWKS publisher, RS256 key rotation, service-account JWT issuer |
| **W3** | Pod-side trust | `ollie-assist` | ✅ | nginx JWKS verifier, `X-Opik-User-API-Key` capability, drop `OLLIE_USER_OPIK_API_KEY` defaulting for SA callers |
| **W4** | Tool surface | `ollie-mcp` | ✅ | 11 tools: `read`, `list`, plus 9 write/agent tools (`ask_ollie`, `score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `run_experiment`, `save_eval_item`) — no MCP resources, per ADR 0004 D1 |
| **W5** | Pod-discovery + cold-start | `ollie-mcp` | ✅ | `/ollie/compute` call, readiness poll, Tasks shaping, blocking-SSE fallback, `Last-Event-ID` resumption |
| **W6** | `opik-mcp` (TS) polish | `opik-mcp` | — | Opik 2.0 surface audit, tool list cleanup, README repositioning |
| **W7** | Self-hosted bundle | DevOps | — | Docker-compose + helm updates, conditional Ollie inclusion, operator docs |
| **W8** | Discovery & verified-host | Discovery | — | CIMD pre-registration (Anthropic / Cursor / Microsoft), MCP registry listing, connect banner |
| **W9** | Observability + SLO | DevOps | — | Prometheus metrics, internal SRE Grafana, alerts, runbook (SLOs in design doc §2.11) |

---

## Host matrix (verified at each release)

We test the four canonical hosts plus MCP Inspector on every release. **Failures gate the release.** Detailed conformance criteria live in design doc §2.11.5.

| Host | OAuth | Tasks | Elicitation | Notes |
|---|---|---|---|---|
| **claude.ai (web)** | Full | Full | Full | Reference host |
| **Claude Code** | Full | Full | Terminal prompts | DCR-only path; tests verified-client gaps (claude-code #38102, #26675) |
| **Cursor** | Full | Blocking-SSE fallback | Partial | Verifies the `ask_ollie` blocking-SSE path |
| **VS Code Copilot** | Full | Blocking-SSE fallback | Partial | Pre-registered clientId path |
| **MCP Inspector** | None (`?token=` for dev) | Full | Full | First-line dev check — `make inspect` opens it |

(MCP Resources are not exercised — reads are tools per ADR 0004 D1.)

---

## Required work

A short build list, grouped by owner. Each item is a deliverable, not a task. Detailed scope per workstream is in design doc §2.18.

### `comet-backend` (Java / Dropwizard)

**New routes** under `comet-rest/.../oauth/mcp/`:

- `/oauth/authorize` (GET + POST consent)
- `/oauth/token`
- `/oauth/register` — DCR with rate limits
- `/oauth/revoke`
- `/oauth/mint-user-api-key` — mTLS-only internal
- `/internal/mcp/audit` — mTLS-only batch endpoint for audit-log ingestion
- Extension of `/api/opik/ollie/compute` to accept service-account JWT callers

(The `/api/admin/mcp/*` endpoint family — connected-clients, usage, audit exports, workspace settings PATCH — is deferred per ADR 0006 and not part of launch.)

**Well-known metadata:**

- `/.well-known/oauth-authorization-server` (RFC 8414)
- `/.well-known/oauth-protected-resource` (RFC 9728)
- `/.well-known/jwks.json` — RS256 JWKS with two-key rotation window

**Service-account JWT issuer** — separate key pair from the user-token signer.

**Liquibase migrations for 6 new MySQL tables:**

- `mcp_oauth_clients`
- `mcp_authorization_codes`
- `mcp_refresh_tokens` (with `family_id` reuse detection)
- `mcp_audit_log` (partitioned by month with row-hash chaining)
- `mcp_mint_audit`
- `mcp_jwks_cache`

(The `mcp_workspace_settings` and `mcp_export_jobs` tables are deferred per ADR 0006 — no per-workspace flag storage and no async audit export at launch.)

**Dropwizard-managed scheduled workers:**

- Daily audit pruner (single global 12-month retention)
- Daily audit archive to WORM S3
- Monthly RS256 key rotation
- Authorization-code sweep

**Misc:** the previously-specified `planTier` extension to `RemoteAuthService.AuthResponse` is no longer needed — it was driven by the deferred retention picker (ADR 0006).

### `ollie-mcp` (Python / FastAPI)

- **New repo:** `comet-ml/ollie-mcp` shipping `ghcr.io/comet-ml/ollie-mcp:<date>-<patch>`
- **Transport:** MCP Streamable HTTP via `modelcontextprotocol/python-sdk` ^1.12
- **Auth:** JWT verifier (RS256 + JWKS cache + Redis-pubsub invalidation) and API-key verifier (`RemoteAuthService` round-trip)
- **Mint client:** mTLS to `comet-backend` + Redis-backed mint cache
- **11 tool implementations:** `read`, `list`, `ask_ollie`, `score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `run_experiment`, `save_eval_item` — no MCP resources (ADR 0004 D1)
- **MCP Tasks engine** for `ask_ollie` and `run_experiment`
- **SSE proxy** translating Ollie's pod event vocabulary into MCP frames
- **Redis state:** session map, event log for `Last-Event-ID` resumption, quota counters, mint cache, revocation bloom
- **Audit:** batched writes to `comet-backend` `/internal/mcp/audit`
- **Prometheus metrics** per design doc §2.11.4

### `ollie-assist` (Python / FastAPI)

- **Pod-side JWT verifier** — nginx + `lua-resty-openidc` + JWKS, sits in front of `/sessions`
- **Multi-tenant key handling** — existing `OLLIE_USER_OPIK_API_KEY` env path stays for browser callers; service-account callers now pass the user's Opik key in `X-Opik-User-API-Key` per-session, so one pod can serve any user the JWT authorizes
- **Stable SSE event vocabulary** (`thinking_delta`, `tool_call_*`, `confirm_required`, `navigate`, `compaction_*`, `message_end`) — pinned as a wire contract that `ollie-mcp` imports types from
- **No agent-logic changes**

### `opik-backend` (Java / Dropwizard)

**No code changes required.** `ollie-mcp` calls existing REST endpoints (`/v1/private/traces`, `/v1/private/spans`, `/v1/private/test-suites/*`, `/v1/private/prompts/*`, `/v1/private/experiments`, comments, scores).

The only ask: **REST wire-format stability** — any breaking REST change should bump a route version and let `ollie-mcp` migrate on its own cadence. The Opik 2.0 surface audit (test-suite-only evaluation entity) is already in.

### `opik-frontend` (React / TS)

- **"Connect to MCP" banner** on workspace page — install URL + per-host instructions (Claude Code, Cursor, claude.ai, VS Code Copilot). This is the only customer-facing MCP UI at launch.
- **OAuth consent screen template** — server-side Freemarker in `comet-backend`, styled to match existing Comet login pages.

The previously-specified workspace "MCP" settings tab (toggles, per-scope disable, retention picker, connected-clients list with revoke buttons, per-tool usage report, audit-log export UI) is deferred per ADR 0006 and is **not** part of launch.

### DevOps / Platform

- **Edge routing** on `www.comet.com` mapping `/api/v1/mcp/*`, `/oauth/*`, `/.well-known/*` to the right backing services
- **`ollie-mcp` Deployment** — always-warm, multi-replica, HPA on the custom `ollie_mcp_sse_active_connections` Prometheus metric, `terminationGracePeriodSeconds: 180` with preStop drain
- **mTLS** between `ollie-mcp` and `comet-backend` via cert-manager `ClusterIssuer`, documented CA-rotation procedure
- **NetworkPolicy** restricting `/oauth/mint-user-api-key` and `/internal/mcp/audit` ingress to the `ollie-mcp` ServiceAccount only
- **S3 buckets:**
  - `comet-mcp-audit-archive` — S3 Object Lock / WORM, 7-year retention (compliance floor)
  - (The previously-specified `comet-mcp-exports` bucket for async audit export downloads is deferred per ADR 0006.)
- **Redis keyspaces** for session, event-log, quota, mint-cache, revocation bloom — sized for projected concurrent SSE connections
- **Observability:** internal SRE Grafana dashboards + Alertmanager rules per design doc §2.11 (customer-facing dashboards deferred per ADR 0006)
- **Runbook:** PagerDuty entries for RS256 key compromise, mTLS CA compromise, audit-chain break

### Discovery & external registration

- **CIMD pre-registration** with Anthropic (claude.ai verified-connector badge); equivalent for Cursor and Microsoft
- **Listing** on `modelcontextprotocol.io` server registry
- **Docs** in `apps/opik-documentation`: OAuth flow walkthrough, per-host install steps, migration guidance from `opik-mcp` (TS) to the hosted MCP

---

## Open questions

1. **Merge `add_test_suite_items` and `save_eval_item`?** Both write to test suites but differ in shape: bulk-with-`source` vs single-with-`target`. Current proposal: keep two clearly-named tools. A polymorphic single tool could reduce surface from 9 → 8 but risks confusing the host LLM more often than it helps. _Decision needed before tool-list freeze._
2. **`create_trace` + `create_span` overlap.** `create_trace` already accepts inline `spans[]`. `create_span` exists only for the "append to existing trace" path. Worth confirming with telemetry from `opik-mcp` (TS) that the second path has real usage. _Worth a 2-day spike on existing usage data._
3. **Self-hosted pod provisioning.** For small self-hosted deployments, is a single static `ollie-assist` pod shared across the install (no codepanels orchestrator) acceptable? Or do we need the full per-user-pod story from day one in self-hosted?
4. **Project-scoped tokens.** Currently workspace-scoped only. Project-scoping was previously framed as "v2 direction" and has been dropped; revisit after 90 days of GA usage data if customers ask for tighter blast radius.
