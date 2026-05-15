# Architecture

This is the condensed version. Full prose is in the team brief at `../../opik/docs/superpowers/specs/2026-05-12-ollie-mcp-team-brief.md`. Wire-level detail is in the design doc next to it.

---

## The stack (Phase 2 — hosted)

```
                                  ┌───────────────────────────────────────┐
External MCP host ──MCP HTTP─────▶│  ollie-mcp  (this repo)               │
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

---

## The stack (Phase 1 — local install)

Strip out everything in `comet-backend` that's OAuth-AS-related. The local `opik-mcp` process runs on the user's laptop, authenticates with an API key, and talks to the same `comet-backend` + `opik-backend` + per-user pod.

```
┌─────────────────┐         ┌────────────────────────────────────────┐
│ MCP host        │         │ opik-mcp (local Python process)        │
│ (Claude Code,   │ ──MCP──▶│ ─────────────────────────────────────  │
│  Cursor,        │ stdio   │  uvx opik-mcp                          │
│  VSCode Copilot)│ or HTTP │  Auth: $OPIK_API_KEY                   │
└─────────────────┘         │  Workspace: $COMET_WORKSPACE           │
                            │  In-memory session/event state         │
                            │  Tool dispatcher (Ollie vs REST)       │
                            └──────────┬──────────┬──────────────────┘
                                       │          │
                  ┌────────────────────┘          └─────────────────────────┐
                  │                                                         │
                  ▼                                                         ▼
        ┌──────────────────┐                                   ┌────────────────────┐
        │ comet-backend    │                                   │ opik-backend       │
        │ /opik/ollie/     │                                   │ /v1/private/*      │
        │  compute-api-key │   (this PR adds API-key auth)     │ (unchanged)        │
        └────────┬─────────┘                                   └────────────────────┘
                 │ provisions on demand
                 ▼
        ┌──────────────────────────┐
        │ ollie-assist pod         │
        │ (one per user × org)     │
        │ ─────────────────────── │
        │ /sessions API + SSE      │
        │ Authenticates via PPAUTH │
        │ cookie (unchanged)       │
        └──────────────────────────┘

claude.ai is NOT supported in Phase 1 (claude.ai doesn't run local MCP servers).
```

**What's saved vs Phase 2:** OAuth AS, JWKS rotation, mTLS, multi-replica Deployment, multi-tenant pods, audit log, central quotas, claude.ai support, CIMD pre-registration. (Customer-facing admin UI is deferred indefinitely — see ADR 0006 — not a Phase 2 deliverable.)

**What's still in scope:** all 11 tools, SSE event vocabulary translation, MCP Tasks primitive, blocking-SSE fallback, cold-start handling, elicitation, host-matrix testing for the 3 supported hosts.

---

## The 11 tools

| # | Tool | Signature | Dispatch | Phase 1 |
|---|---|---|---|---|
| 1 | `read` | `(entity_type, id, max_tokens?)` | `opik-backend` REST | ✅ |
| 2 | `list` | `(entity_type, name?, page?, size?)` | `opik-backend` REST | ✅ |
| 3 | `ask_ollie` | `(query, page_context?, attach_resources?, thread_id?)` | Ollie pod | ✅ (cloud only) |
| 4 | `score` | `(target, name, value, reason?)` | `opik-backend` REST | ✅ |
| 5 | `comment` | `(target, text)` | `opik-backend` REST | ✅ |
| 6 | `add_test_suite_items` | `(test_suite_id, items, source?)` | `opik-backend` REST | ✅ |
| 7 | `save_prompt_version` | `(prompt_id?, name?, template, metadata?)` | `opik-backend` REST | ✅ |
| 8 | `create_trace` | `(trace)` (accepts inline `spans[]`) | `opik-backend` REST | ✅ |
| 9 | `create_span` | `(trace_id, span)` | `opik-backend` REST | ✅ |
| 10 | `run_experiment` | `(experiment_config)` | Ollie pod | ✅ (cloud only) |
| 11 | `save_eval_item` | `(target, expected, metadata?)` | `opik-backend` REST | ✅ |

**Bucketing (JTBD):** Read / Investigate / Annotate / Curate / Author / Iterate.

**Why no `create_thread`:** threads in Opik are not a standalone entity — they're the `thread_id` field on a trace. No `POST /v1/private/threads` to wrap. Hosts start a thread by calling `create_trace` with a fresh `thread_id`.

**Why no MCP Resources:** ADR 0004 D1. Claude Code (our primary host) does not surface resources to the LLM at all, and Cursor only renders them as `@`-mention completions. With ~95% of mid-session usage being reads, putting reads behind a host-invisible surface defeats the purpose. The `read` and `list` tools cover the same ground and compose with writes in a single tool-calling loop. The `read` tool accepts `opik://<entity_type>/<id>` URIs as the `id` parameter for forward-compat with any future resource-aware client.

---

## The read surface (`read` / `list`)

Two universal tools cover all entity reads. Readable entity types: trace, span, project, thread, dataset, dataset_item, experiment, experiment_item, prompt, prompt_version, test_suite, feedback_definition, automation_rule_evaluator, annotation_queue, alert, dashboard, optimization, workspace_configuration. Composite entities (trace, prompt) inline their child collections.

`list` supports a subset of the readable types with `(name?, page, size)` pagination: project, dataset, experiment, prompt, feedback_definition, automation_rule_evaluator, annotation_queue, dashboard, optimization, test_suite. Output is a pipe-delimited table with a pagination footer when `total > page * size`.

---

## Cold start, MCP Tasks, SSE proxy

The single hardest UX problem: when a user first calls `ask_ollie`, their per-user pod may not exist yet. Helm install + image pull + warmup can take up to **2 minutes**. MCP hosts time out at **~30 s**. Solution: **MCP Tasks primitive** (2025-11-25 spec) with a blocking-SSE fallback.

Sequence (Tasks-capable host):

```
1. Host  → tools/call { name:"ask_ollie", arguments:{...},
                        _meta: { task: { ttl: 600000 } } }
2. opik-mcp → /opik/ollie/compute-api-key (Authorization: <api-key>)
              → returns {computeURL, enabled} + Set-Cookie: PPAUTH=<token>
3. opik-mcp → CreateTaskResult (returned in <2 s, before pod is awake)
4. Host LLM tells user "Ollie is starting..."
5. opik-mcp (async): polls pod /health/ready with Cookie: PPAUTH=<token>
6. opik-mcp → notifications/tasks/updated ("Pod ready, calling agent...")
7. opik-mcp → POST pod/sessions  (creates session, opens SSE)
8. opik-mcp ← pod SSE events (thinking, tool_use, ...)
9. opik-mcp → notifications/tasks/updated per event
10. On pod final: mark task status:"completed" with CallToolResult.
```

Fallback (host doesn't advertise Tasks): hold `tools/call` HTTP response open as SSE, emit `notifications/progress` every 10 s, resolve with `CallToolResult` when done. Identical producer code path; only response shape differs.

**Resumability:** `notifications/progress` and SSE events both carry monotonic `seq` IDs. On disconnect, host reconnects with `Last-Event-ID: <seq>`; server replays missed events. In Phase 1 this is in-memory and best-effort (single replica). In Phase 2, Redis-backed and replica-failover-transparent.

---

## Anchor libraries — don't hand-roll what's solved

| Library | Role |
|---|---|
| `mcp` (`modelcontextprotocol/python-sdk` ^1.12) | Streamable HTTP transport, OAuth resource-server validation, experimental Tasks engine |
| `joserfc` | JWT/JWKS with multi-`kid` rotation (Phase 2) |
| `httpx-sse` | Consumes upstream pod SSE |
| `redis-py` (async) | Sessions, event log, mint cache (Phase 2) |
| `datamodel-code-generator` | Builds typed Opik client from OpenAPI at CI time |
| `sse-starlette` | Serves the blocking-SSE fallback |
| MCP Inspector (`make inspect`) | Manual smoke; SDK's vendored conformance fixtures gate CI |

---

## Auth headers (Phase 1)

| Hop | Headers |
|---|---|
| Host → `opik-mcp` | none (local stdio or `localhost` HTTP) |
| `opik-mcp` → `comet-backend` | `Authorization: <COMET_API_KEY>` + `Comet-Workspace: <ws>` |
| `comet-backend` response | body `{computeURL, enabled}` + `Set-Cookie: PPAUTH=<browserAuth>` |
| `opik-mcp` → pod `/health/ready` | `Cookie: PPAUTH=<browserAuth>` |
| `opik-mcp` → pod `/sessions` | `Cookie: PPAUTH=<browserAuth>` |
| `opik-mcp` → `opik-backend` | `Authorization: <OPIK_API_KEY>` + `Comet-Workspace: <ws>` |

**Note:** `OPIK_API_KEY == COMET_API_KEY` in cloud Comet (same key DB, validated via `comet-backend /opik/auth`). Phase 1 standardizes on a single env var; recommend `OPIK_API_KEY`.

All tools use HTTP REST. There is **no** shelling-out to the `opik` CLI from `opik-mcp` — every call is in-process `httpx`.

---

## What is NOT in this repo

- `ollie-assist` — the per-user pod (separate repo). We import the SSE event types from it.
- `comet-backend` — the OAuth AS and pod orchestrator (separate repo). We talk to it over HTTP.
- `opik-backend` — the REST upstream (in the `opik` monorepo). We talk to it over HTTP. Zero code changes needed.
- `opik-frontend` — the React app. No MCP-specific changes are scheduled — the customer-facing admin tab is deferred per ADR 0006. (An in-Opik "Connect" install banner is in scope as a small piece of install UX, independent of the admin deferral.)
- `opik-mcp` (TypeScript) — the existing scripted/CI/no-LLM path. Stays where it is.
