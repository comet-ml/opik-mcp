# Ollie MCP — Hosted, OAuth-Authed, Ollie-Powered

**Jira:** [OPIK-6439](https://comet-ml.atlassian.net/browse/OPIK-6439) — *Improve Opik MCP server and include Ollie tool*
**Status:** Production-ready design, pre-implementation
**Owner:** yaroslavb@comet.com
**Date:** 2026-05-12
**Audience:** Part 1 = product / stakeholder review (shareable). Part 2 = engineering implementation.

This design follows the team brief at [`team-brief.md`](./team-brief.md). The brief is the shareable narrative; this doc is the engineering source-of-truth.

> **Phase split (added after initial write):** This document originally described the hosted/OAuth target state as a single shippable. Product has since asked for a Phase 1 local-install (API-key auth) build first, then the hosted/OAuth Phase 2 after. See [`phase-1.md`](./phase-1.md) and [`auth-flow.md`](./auth-flow.md) for the Phase 1 scope and the single backend patch ([comet-ml/comet-backend#5555](https://github.com/comet-ml/comet-backend/pull/5555)) that enables it. The contents below remain the canonical Phase 2 design; Phase 1 is a strict subset (no OAuth, no JWT, no hosted Deployment, no admin UI, no Redis, no central audit tables).

---

## Table of contents

**Part 1 — Product**
1.1 Problem · 1.2 Goals · 1.3 User experience · 1.4 Endpoints · 1.5 Tool surface · 1.6 Resource surface · 1.7 Prompts · 1.8 Auth flows · 1.9 Quotas & cost model · 1.10 Self-hosted story · 1.11 `opik-mcp` (TS) — polished, not deprecated · 1.12 Discovery surface · 1.13 Admin surface · 1.14 Launch checklist · 1.15 Out of scope · 1.16 Why these choices — evidence · 1.17 Decisions still open for the meeting

**Part 2 — Engineering**
2.1 Architecture · 2.2 Repo-by-repo deltas · 2.3 OAuth flow · 2.4 Streamable HTTP details · 2.5 `ask_ollie` lifecycle (cold-start + Tasks + SSE proxy) · 2.5.1 User identity propagation · 2.5.2 Pod-side JWT verifier · 2.6 Resources implementation · 2.7 Opik 2.0 audit deliverable · 2.8 Self-hosted packaging · 2.9 Comet Cloud deployment · 2.10 Security · 2.11 Observability & SLO · 2.12 Compliance · 2.13 Error response taxonomy · 2.14 Contract testing & schema source-of-truth · 2.15 Developer workflow · 2.16 Testing strategy · 2.17 Risks · 2.18 Workstreams · 2.19 References

---

# Part 1 — Product

## 1.0 Acronyms (read once)

- **MCP** — Model Context Protocol (open spec for AI host ↔ tool server wire).
- **JTBD** — Jobs-To-Be-Done analysis (used to bucket the nine tools in §1.3 / §1.5).
- **AS** — Authorization Server (the OAuth endpoint that issues tokens; lives on `comet-backend`).
- **PKCE** — Proof Key for Code Exchange (RFC 7636).
- **DCR** — Dynamic Client Registration (RFC 7591).
- **CIMD** — Client ID Metadata Document (MCP SEP-991, Nov 2025; pre-verified hosts).
- **SEP** — Specification Enhancement Proposal (MCP's spec-change RFC process).
- **JWKS** — JSON Web Key Set (the `/.well-known/jwks.json` public-key document).
- **mTLS** — mutual TLS (both ends present certificates).
- **DPA** — Data Processing Addendum (legal annex for processor obligations).

## 1.1 Problem

Today the only way to use Opik from an AI coding host (Claude Code, Cursor, claude.ai, VS Code Copilot) is to install `opik-mcp` locally via `npx -y opik-mcp` and hand-edit a JSON config with an Opik API key. That is a friction wall for cloud users and it exposes a 30+-tool surface that bloats the host LLM's context and tanks tool-selection accuracy. Meanwhile **Ollie**, our Opik-domain expert agent, is only reachable from inside the Opik web UI — every external AI host is blind to it.

## 1.2 Goals

- **No local install.** Users add Opik to Claude Code / Cursor / claude.ai by clicking "connect" and approving consent in the browser. No `npx`, no `uvx`, no JSON config beyond a URL.
- **Reuse the existing Comet login.** A user already signed into `comet.com` does not log in again to grant MCP consent.
- **Lean tool surface.** Nine outcome-oriented tools chosen via JTBD analysis (§1.3); host LLM's context budget stays intact, tool-selection accuracy stays high.
- **`ask_ollie` is the doorway, not the only door.** A single agentic tool gives external hosts access to Ollie's full intelligence (session memory, sub-agents, entity reads, navigate hints, test-suite writes, eval runs) for the investigative/multi-step half of the workload. The eight deterministic writes (`score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `run_experiment`, `save_eval_item`) sit alongside it so hosts can plan them directly without an Ollie hop.
- **Self-hosted Opik keeps working.** No regression for OSS users; they get the same hosted experience when they bring their own Anthropic key, and the deterministic-only surface otherwise.
- **Pass the Opik 2.0 audit.** Whatever ships is verified against the Opik 2.0 info architecture (workspaces, projects, evaluation surface — Test Suites + generic Datasets, prompts).

Non-goals: a generic MCP gateway, a CLI, deprecating `opik-mcp`, re-implementing Comet's auth system, a customer-facing admin UI or workspace-MCP-settings dashboard (deferred — see ADR 0006).

## 1.3 User experience (the shareable picture)

### Onboarding (~30 seconds, browser-only)

1. User opens **Claude Code / Cursor / claude.ai → Settings → Connectors → Add custom MCP server**.
2. Enters one URL: `https://www.comet.com/api/v1/mcp`.
3. Browser redirects to `https://www.comet.com/oauth/authorize`.
4. Comet recognizes the existing `sessionToken` cookie → user already logged in → consent screen ("Cursor wants to access workspace X with these permissions…"). User picks a workspace, confirms.
5. Lands back in the MCP host. Connector is live.

Users in multiple workspaces add one connector per workspace (each gets a connector named "Opik — &lt;workspace&gt;"). For headless / scripted use (CI, `claude` non-interactive), the same endpoint accepts `Authorization: Bearer <opik-api-key>` — same protocol, no consent flow.

### What the user actually does mid-session

The user is talking to their **host LLM**, not to Ollie. Ollie is plumbing. From the user's POV, "Claude can now read and act on my Opik data." Five jobs-to-be-done, with estimated frequency from interviews + analogous Opik UI session telemetry:

| Bucket | Example phrasing | Frequency | What happens |
|---|---|---|---|
| **Investigate ("why?")** | "Why are my prod traces failing today?" / "Which prompt version is winning?" | ~50% | Host LLM calls `ask_ollie`; Ollie reads traces/experiments, plans, returns a summary. |
| **Curate ("save this")** | "Save this trace as a regression test" / "Add this conversation to my eval suite" | ~20% | Host LLM calls a direct write tool (`save_eval_item`, `add_test_suite_items`) — no Ollie hop. |
| **Iterate ("run it")** | "Run my eval suite" / "Re-run experiment X with the new prompt" | ~15% | Direct call to `run_experiment`. Synthesis follow-up goes through `ask_ollie`. |
| **Annotate ("flag it")** | "Comment 'check auth subagent here'" / "Score this 1/5 with reason" | ~10% | Direct call to `comment` / `score`. |
| **Author ("help me set up")** | "Help me write an eval for this agent" | ~5% | `ask_ollie`. |

Two design implications:

- **`ask_ollie` is the doorway, not the destination.** ~55% of calls are direct tools the host LLM plans without Ollie round-tripping. Routing those through `ask_ollie` would burn a 5–15 s LLM hop on every "save this trace".
- **The user never sees Ollie's session.** Memory threading is an internal optimization (see §1.5 `thread_id`). The user's only conversation is with the host LLM.

### Worked example: "why are traces failing in foo?"

> 1. User: "look at my prod traces from today, anything failing?"
> 2. Cursor calls `ask_ollie("recent failures in project foo")`. The MCP server returns `CreateTaskResult` in <2 s; Cursor renders "Ollie is starting…" Streaming `thinking` + progress frames arrive as the pod warms.
> 3. Ollie returns a 3-bullet summary + 5 trace IDs.
> 4. User: "show me the worst one." Cursor already has the IDs — it calls `read("trace", <id>)` and renders the response inline.
> 5. User: "save this as a regression test." Cursor calls `save_eval_item(target={type:"trace", id:trace_id}, expected={...})`. MCP elicitation gates the write; user clicks Approve. Returns item ID.
> 6. User: "now re-run my eval suite." Cursor calls `run_experiment(...)`. Tasks primitive: `CreateTaskResult` immediately; final `CallToolResult` lives on the Task object and is retrieved via `tasks/get` when status flips to `completed`.

Four MCP calls (one agentic tool, one `read` tool, two direct writes), no user-visible Ollie session.

## 1.4 Endpoints

The MCP entrypoint and OAuth metadata both live under `comet.com` so the existing session cookie reaches the authorize step.

| Path | Lives in | Purpose |
|---|---|---|
| `GET /.well-known/oauth-protected-resource` | `comet-backend` | RFC 9728 protected-resource metadata. Points at the AS. |
| `GET /.well-known/oauth-authorization-server` | `comet-backend` | RFC 8414 AS metadata: authorize/token/register/revoke endpoints, supported scopes, supported response/grant types, PKCE methods. |
| `GET /.well-known/jwks.json` | `comet-backend` | Public keys for the RS256 access-token signer. Rotates monthly. |
| `GET /oauth/authorize` | `comet-backend` | OAuth 2.1 authorize endpoint. Reads `sessionToken` cookie; renders consent screen; redirects with code. |
| `POST /oauth/token` | `comet-backend` | Code → access_token + refresh_token. PKCE + refresh rotation. |
| `POST /oauth/revoke` | `comet-backend` | RFC 7009 token revocation. |
| `POST /oauth/register` | `comet-backend` | Dynamic Client Registration (RFC 7591). Also accepts Client ID Metadata Documents (SEP-991, Nov 2025). |
| `POST /oauth/mint-user-api-key` | `comet-backend`, **mTLS-only** | Internal endpoint for `ollie-mcp` to mint a per-session Opik user API key from an OAuth JWT. |
| `POST, GET /api/v1/mcp` | `ollie-mcp` | Streamable HTTP MCP entrypoint. `POST` = JSON-RPC frames (including `notifications/cancelled` for in-band task cancellation per MCP spec); `GET` = SSE notification stream + `Last-Event-ID` resumption. |
| `GET /health`, `/healthz`, `/ready`, `/metrics` | `ollie-mcp` | Liveness, readiness, Prometheus. |

Self-hosted Opik exposes only the `ollie-mcp` paths (API-key gated by default; OAuth optional via operator-configured IdP).

## 1.5 Tool surface

Eleven tools, organized by the JTBD buckets from §1.3, plus two universal reads. `ask_ollie` is the **doorway for investigate / synthesize / multi-step** jobs; `read` / `list` cover the **show-me-X / what-is-Y** hot path; the eight remaining tools are the deterministic writes a host LLM can plan directly.

Three sources of truth shape this surface, in order:
1. **JTBD buckets** — Investigate, Curate, Iterate, Annotate, Author.
2. **Opik 2.0 surface** — **Test Suites are the only evaluation entity** (`type=evaluation_suite`, REST path `/v1/private/test-suites/*`). The legacy generic-dataset concept has been retired in Opik 2.0; references to "datasets" in older docs map 1:1 onto test suites. All eval-row writes and item curation target test suites.
3. **Python SDK naming** — `client.trace()` / `client.span()`. The `log_*` prefix in the SDK is feedback-scoring only; programmatic ingestion is `client.trace()` / `client.span()`, hence the tool names `create_trace` and `create_span` (not `log_*`).

| # | Bucket | Tool | Signature | Goes to |
|---|---|---|---|---|
| 1 | Investigate / Author | `ask_ollie` | `(query, page_context?, attach_resources?, thread_id?, continuation_token?)` | per-user `ollie-assist` pod |
| 2 | Read | `read` | `(entity_type, id, max_tokens?)` | `opik-backend` REST (composite for trace+spans / prompt+versions) |
| 3 | Read | `list` | `(entity_type, name?, page?, size?, project_id?, test_suite_id?, prompt_id?)` | `opik-backend` REST |
| 4 | Annotate | `score` | `(target, name, value, reason?)` | `opik-backend` REST |
| 5 | Annotate | `comment` | `(target, text)` | `opik-backend` REST |
| 6 | Curate | `add_test_suite_items` | `(test_suite_id, items, source?)` | `opik-backend` REST `POST /v1/private/test-suites/{id}/items` |
| 7 | Curate | `save_prompt_version` | `(prompt_id?, name?, template, metadata?)` | `opik-backend` REST `POST /v1/private/prompts/versions` |
| 8 | Author | `create_trace` | `(trace, project_name?)` | `opik-backend` REST `POST /v1/private/traces` |
| 9 | Author | `create_span` | `(trace_id, span, project_name?)` | `opik-backend` REST `POST /v1/private/spans` |
| 10 | Iterate | `run_experiment` | `(experiment_config)` | per-user pod (long-running, Tasks primitive) |
| 11 | Iterate | `save_eval_item` | `(target, expected, metadata?)` | `opik-backend` REST `POST /v1/private/test-suites/{id}/items` |

**Why reads are tools, not resources.** Most MCP hosts (Claude Code today; Cursor partially) don't surface the resources primitive to the LLM at all, so a resource-only read surface bottoms out the value prop for ~95% of mid-session usage. `read` and `list` are universal-by-entity-type and mirror ollie-assist's `ENTITY_REGISTRY` 1:1 (eight Phase-1 entities: project, trace, span, test_suite, experiment, prompt, test_suite_item, prompt_version). Full rationale and trade-offs in [ADR 0004 D1](decisions/0004-tool-surface.md#d1-reads--universal-tools-move-from-resources-to-tools). `opik://` URIs remain accepted as `id` input for forward-compat with any future resource-aware host.

**`add_test_suite_items` vs `save_eval_item`.** Both write rows to a test suite, but the shapes differ. `add_test_suite_items` is the **bulk-curation** path: pass a `test_suite_id` plus an `items` array (each `{input, expected_output?, metadata?}`) plus an optional `source` (`{kind: "traces", trace_ids: [...]}` to seed rows from telemetry). `save_eval_item` is the **inline single-row** path: pass a `target` (a trace/span/thread) plus an `expected` value, and the server materializes one test-suite row from that target. The two tools stay separate because the parameter shapes differ enough that a single polymorphic tool would confuse the host LLM more often than two clearly named ones.

**`save_prompt_version` semantics.** Either create a brand-new prompt (omit `prompt_id`, provide `name`) or add a new version to an existing prompt (provide `prompt_id`, optionally omit `name`). The server populates `commit_message` from `metadata.commit_message` if set, otherwise auto-generates `"Saved via MCP by {user_email}"`. Version numbering is server-side (incremental); the response carries `{prompt_id, version, created_at}`. Calling `save_prompt_version` with `prompt_id` referring to a prompt in a different workspace is a 404 (not a 403, to avoid leaking existence).

**`run_experiment` input schema.** Single `experiment_config` argument, shape:
```json
{
  "test_suite_id": "<uuid>",                    // required
  "prompt_id": "<uuid>",                        // optional; refers to a saved prompt version
  "prompt_template": "<string>",                // optional; inline alternative to prompt_id
  "scorers": ["hallucination", "answer_relevance", "..."],  // metric names from opik.evaluation.metrics
  "model": "claude-sonnet-4-6" | "gpt-4o" | ...,            // LLM-as-judge model for scorers that need one
  "concurrency": 8,                             // optional; default 8, max 32
  "experiment_name": "<string>",                // optional; defaults to "MCP-run-{timestamp}"
  "metadata": { /* free-form */ }
}
```
The Tasks-engine work function streams `notifications/tasks/updated` with `statusMessage: "Scoring item N/M..."` and final `CallToolResult` includes `experiment_id` and `summary_url` so the host can deep-link the user into the Opik UI. Schema validation on the `ollie-mcp` side rejects unknown scorer names against the per-version Opik metric catalog (loaded from `opik.evaluation.metrics.__all__` at server boot).

**Why nine and not three.** §1.3 shows roughly half of mid-session actions are deterministic Annotate/Curate/Iterate writes that the host LLM can plan directly. Routing those through `ask_ollie` would burn a 5–15 s LLM hop for a one-line write — worse UX than the latency saved by a smaller tool list.

**Why `create_span` is separate from `create_trace`.** `create_trace` accepts an optional inline `spans` array, so a one-shot "trace + N spans" call goes through that tool. `create_span` exists for the **append-span-to-existing-trace** flow — a host LLM that has already created (or has a handle to) a trace can attach a span to it without re-sending the whole trace. The two map cleanly onto the two REST endpoints (`POST /v1/private/traces` vs `POST /v1/private/spans`); collapsing them would force the dispatcher to disambiguate by shape, which is exactly the kind of polymorphism we avoid (cf. `add_test_suite_items` vs `save_eval_item`).

**Why there is no `create_thread`.** Threads in Opik are not a standalone entity — a "thread" is the `thread_id` field on a trace, and the thread surfaces (annotation, scoring, listing) all derive from grouping traces by that field. There is no `POST /v1/private/threads` REST endpoint to wrap. To start a new thread, a host LLM calls `create_trace` (or `create_span`) with a fresh `thread_id`; to extend a thread, it reuses the same `thread_id` on subsequent traces. The `score` and `comment` tools accept `target.type = "thread"` because the **read/annotation** surfaces for threads are real — only the *create* primitive doesn't exist.

**Why not per-entity `list_*` / `get_*`.** Reads collapse into the two universal tools `read` and `list` rather than 18 per-entity getters — same `ENTITY_REGISTRY` codepath as ollie-assist (`src/opik_mcp/read_list/registry.py`). One schema covers N entities; the host LLM's tool-selection prompt stays small (two read entries, not eighteen). Entity-specific knobs (composite trace+spans, prompt+versions, project-scoped trace lists) live inside the dispatcher.

**`target` shape (used by `score`, `comment`, `save_eval_item`)** — uniform `{type: "trace" | "span" | "thread", id: string}`. The tool dispatcher maps `type` to the correct opik-backend path (`/v1/private/traces/{id}/comments` vs `/v1/private/spans/{id}/comments` etc). Unknown `type` is a `400 invalid_argument` before any REST call. There is no `"dataset"` target — items live inside a test suite and are referenced by `test_suite_id` on the dedicated tools.

**Scoping: workspace is ambient, project is per-call where REST takes it.** No tool takes a `workspace` argument. Workspace is bound to the auth context (OAuth grant per design §1.8 / API-key path resolved via `Comet-Workspace` header from `COMET_WORKSPACE`), so the host LLM never has to know or pick one. Project is *only* an argument on the two write tools where Opik's REST body actually accepts it — `create_trace` (optional `project_name` on `POST /v1/private/traces`, defaults to "Default Project") and `create_span` (same on `POST /v1/private/spans`). All other tools are either entity-scoped (`score`, `comment` use the trace/span id directly) or workspace-scoped (`save_prompt_version`, `add_test_suite_items`, `save_eval_item`, `run_experiment` — prompts, test suites, and experiments are workspace-level entities, see `PromptResource.java:94`, `DatasetsResource.java:199`, `ExperimentsResource.java:300`). The MCP server adds no project-selection state of its own — there is no `set_project` tool and no session-level project setting. Cross-project writes in a single host session work without any switching dance.

**Why no polymorphic CRUD tools.** A `trace(action: "create" | "read" | "delete", ...)` tool would collapse the count but not the LLM's decision cost — the host still has to pick `action` correctly, with worse signal than two clearly named tools provide. The collapsed shape also forces parameter unions that bloat the input schema and confuse schema-driven validation. The single-purpose split also matches Ollie's own internal tool surface (`ollie-assist/src/ollie_assist/tools/`: `read`, `list`, `search`, `add_ollie_catches_item`, etc. — none of Ollie's 14 tools use an action discriminator), so the same patterns the host LLM learns from `opik-mcp` transfer to the agent it eventually delegates to.

**`ask_ollie` arguments — clarifications:**
- `query` (required, string) — natural-language question.
- `page_context` (optional, string) — free-form context the host already has (e.g., the project page the user is looking at).
- `attach_resources` (optional, string[]) — list of `opik://` URIs the host wants Ollie to consider. The server pre-resolves each URI (subject to scope + visibility checks) using the same `read_list/uri.py` parser the `read` tool uses, then hands materialized content to Ollie. Phase 1 surface; accepted but currently a no-op pending the `ollie-assist` pod's `ChatRequest` schema gaining the field — no host changes needed when the pod side lands.
- `thread_id` (optional, string) — omit to start a fresh Ollie session; pass a prior response's value to continue. Invisible to end-users — host-LLM-managed cost optimization. The tool description instructs hosts to reuse `thread_id` for follow-ups and omit it for unrelated questions. Idle Ollie sessions expire after 30 min (§2.5); a stale `thread_id` returns `thread_expired`.
- `continuation_token` (optional, string) — set when a prior `ask_ollie` response was truncated; fetches the remainder (§2.5).

That is the entire tool surface visible to the host LLM. No `list_projects`, no `get_trace_by_id`, no `expert_*_actions`. Reads sit behind `read` / `list` (§1.6); synthesis-heavy work goes through `ask_ollie`.

### `ask_ollie` tool description

The text the host LLM reads when deciding whether to call the tool:

> **`ask_ollie`** — Ask Ollie, an AI expert on Opik (LLM observability platform), to help with traces, spans, test suites, experiments, prompts, and evaluations. Use this when the user asks **why** something happened, wants a summary or comparison across many entities, or needs help authoring an eval, prompt, or instrumentation. Ollie has access to the user's workspace data and can read, summarize, and write directly. Pass natural-language queries; Ollie plans the investigation. **Reuse `thread_id`** from a prior `ask_ollie` response when continuing the same investigation ("ok now group those by model"); omit it for a fresh question. Prefer direct write tools (`score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `save_eval_item`, `run_experiment`) when the user's intent is concrete and well-defined — those don't need an LLM hop. Prefer `read` / `list` when the question is pure lookup.

Writes Ollie performs mid-stream auto-execute without a per-action confirmation step (YOLO mode, see [ADR 0005](decisions/0005-ask-ollie-yolo-mode.md)); auto-approvals are written to the `opik_mcp.audit` Python logger.

When Ollie isn't available (workspace `ask_ollie_enabled=false`, self-hosted without `ANTHROPIC_API_KEY`), this tool is **omitted** from `tools/list` — not returned with an error. The remaining tools stay available.

### `read` / `list` tool descriptions

The text the host LLM reads when deciding whether to call each tool (rendered from the docstrings in `src/opik_mcp/server.py`):

> **`read`** — Read any Opik entity by ID, name, or `opik://` URI, with adaptive compression. Prefer a UUID (single API call, unambiguous); name lookup is available for `project`, `experiment`, `prompt`, `test_suite` (slower; may return multiple candidates). Special shapes: `trace` returns `{trace, spans, spansTruncated}` with up to 200 spans inlined; `prompt` returns `{prompt, versions, versionsTruncated}` with up to 100 versions. Output is a `[read: …]` size header (entity_type, id, compression tier, returned/full tokens) followed by compact JSON.

> **`list`** — List Opik entities with optional name filter and pagination. Pipe-delimited table (id, name, plus a few entity-specific columns) with a pagination footer. Project-scoped types require their parent id (`trace` → `project_id`, `test_suite_item` → `test_suite_id`, `prompt_version` → `prompt_id`). Workspace-wide types (`project`, `experiment`, `prompt`, `test_suite`) accept an optional `name` substring filter.

### Project scoping

Workspace is ambient (bound to auth at session start); project is per-call where the REST endpoint accepts it. **The MCP server holds no project state** — there is no `set_project` tool and no session-level project lock. Every tool that takes a project is stateless: the LLM must pass it on each call. This is deliberate and keeps Phase 2 (multi-tenant streamable HTTP) compatible — one MCP process can fan out across tenants without the per-session sticky-context gymnastics that single-tenant servers like ollie-assist need.

**Name-only on writes.** Write/agent tools (`ask_ollie`, `score`, future `create_trace`/`create_span`) expose `project_name` only — no `project_id` variant. This matches the Opik Python and TypeScript SDKs, which only surface `project_name` on every public write method. The opik-backend write DTOs (`Trace.java`, `Span.java`) annotate `projectId` as `READ_ONLY`; it's a filter param on list endpoints, not a write-side identifier. UUIDs are still accepted as parent ids on `list` (e.g. `list("trace", project_id=…)`) because list endpoints take them as query filters — that's a read concept.

Where project surfaces:
- **`ask_ollie`** — optional `project_name` goes into Ollie's structured `context` envelope on the pod's `POST /sessions` (`ChatRequest.context: PageContext` in ollie-assist `types/chat.py`). Ollie's read tools resolve the name to an id server-side via `session.project_name` (no `<current_project>` block is rendered into the system prompt — project scope only surfaces through tool calls). Ollie does **not** persist project across messages within a thread — passing `thread_id` for a follow-up does not carry project forward, so the LLM must re-send it on every continuation if it wants the same scope.
- **`list`** — project-scoped entity types (`trace`, `test_suite_item`, `prompt_version`) require their parent id (`project_id`, `test_suite_id`, `prompt_id`) — list endpoints filter by UUID. Workspace-wide types accept an optional `name` filter only.
- **`score`** on `thread` targets — accepts `project_name`. Opik's batch thread-feedback endpoint takes it to disambiguate threads that share an id across projects. Ignored for trace/span targets — those are entity-implicit.
- **`create_trace` / `create_span`** — optional `project_name` on the REST body; defaults to "Default Project" server-side.

**Default project hint.** When `OPIK_DEFAULT_PROJECT_NAME` is set in the server's environment, the `InitializeResult.instructions` blob (ADR 0004 D6) tells the LLM to pass it as `project_name` on every tool call unless the user explicitly names a different project. This is a one-time priming hint — the server doesn't enforce it, doesn't auto-inject it, doesn't track whether the LLM honors it. If the LLM ignores the hint, calls land workspace-wide; that's a correct, observable outcome, not a silent bug.

## 1.6 Read surface

Two tools — `read` and `list` — universal over an entity registry. **No MCP `resources` primitive is published** (see [ADR 0004 D1](decisions/0004-tool-surface.md#d1-reads--universal-tools-move-from-resources-to-tools); Claude Code doesn't surface resources to the LLM, Cursor only as mention completions). Reads compose with writes in one tool-calling loop instead of needing separate resource-fetch + tool-call cycles.

| Entity type | `read` | `list` | List requires | Notes |
|---|---|---|---|---|
| `project` | ✅ (UUID or name) | ✅ name filter | — | Singleton record + workspace stats. |
| `trace` | ✅ (UUID only) | ✅ | `project_id` | Composite: trace + up to 200 spans inlined. |
| `span` | ✅ (UUID only) | — | — | Singleton span. Use `list('trace', project_id=…)` to discover. |
| `test_suite` | ✅ (UUID or name) | ✅ name filter | — | Opik 2.0 evaluation entity; REST path `/v1/private/datasets/{id}`. |
| `experiment` | ✅ (UUID or name) | ✅ name filter | — | Status + summary scores. |
| `prompt` | ✅ (UUID or name) | ✅ name filter | — | Composite: prompt + up to 100 versions inlined. |
| `test_suite_item` | — (list-only) | ✅ | `test_suite_id` | Items addressed via parent; use `read('test_suite', id)` for inline rows. |
| `prompt_version` | — (list-only) | ✅ | `prompt_id` | Versions addressed via parent; use `read('prompt', id)` for inline versions. |

**Eight Phase-1 entities.** Source of truth is `src/opik_mcp/read_list/registry.py::ENTITY_REGISTRY`. Adding an entity is a one-entry diff (fetch + optional list + optional name-search plug into the existing dispatchers).

**`opik://` URIs as `id` input.** `read` accepts a `opik://traces/<uuid>` style URI in the `id` parameter; the parser overrides `entity_type` from the URI prefix. This is the forward-compat affordance from ADR 0004 D1 — any future resource-aware client can hand the URI straight to the read tool. Recognized shapes: `projects`, `traces`, `spans`, `test-suites`, `experiments`, `prompts`.

**Adaptive compression.** Output is a one-line `[read: …]` header (entity_type, id, tier, returned tokens, full tokens) followed by compact JSON. Tiers (`FULL` / `MEDIUM` / `SKELETON`) are selected by token budget — default ~8k tokens, overridable per call via `max_tokens`. `MEDIUM` truncates long string fields with jq path hints; `SKELETON` (trace composite only today) drops payloads but keeps the span tree so the LLM can drill in via `read('span', id)`.

**Pagination envelope.** `list` returns a pipe-delimited table with a pagination footer (`Use page=N for next M results.`). Spring Page envelope from opik-backend (`content`, `page`, `size`, `total`) is rendered without translation. There is no opaque cursor — `(page, size)` is the contract.

### `InitializeResult.instructions` (ADR 0004 D6)

Rendered per-session in `src/opik_mcp/instructions.py` and delivered on the MCP `initialize` handshake. Hosts that support the field (Claude Code, Cursor, VS Code, Goose) inject the blob as system-prompt-like context; hosts that ignore it lose nothing — every tool's description remains self-contained.

Phase 1 template (static; `user_email` lands when OAuth/identity is wired up in Phase 2):

```
You're connected to Opik (Comet's LLM observability platform){ as <user_email>}
in workspace "<workspace>". The Opik UI is at <opik_url>.

Tool selection:
- read / list: use for any "show me X" or "what is Y" — these are the cheapest reads.
- Direct writes (score, comment): use when the user's intent is concrete and well-defined.
- ask_ollie: use for investigative questions, cross-entity synthesis, or domain expertise.

Today's date is <YYYY-MM-DD>.
```

GitHub MCP's published data measured **+25pp workflow adherence on capable models, +60pp on smaller models** with dynamic per-session instructions — the highest-leverage single dial on tool-selection quality.

## 1.7 Prompts (MCP `prompts` primitive)

Two seeded templates so users can one-click common flows:

- `investigate-failure(project_id, time_window?)` — opens a session with Ollie pre-primed to triage error traces.
- `compare-experiments(experiment_ids[])` — Ollie reads, computes deltas, summarizes.

Both prompts internally invoke `ask_ollie`, so they consume `ask_ollie` quota (one call per prompt run). Host support for the MCP `prompts` primitive varies — when unsupported, the same flow is available by typing the equivalent question into `ask_ollie`.

## 1.8 Auth flows

| Path | Used by | How |
|---|---|---|
| **OAuth 2.1 + PKCE** | claude.ai, Cursor, Claude Code (browser), VS Code | Cookie-aware authorize on `comet.com`. DCR + pre-registered + CIMD. Refresh tokens with rotation. Access token = **RS256-signed JWT**, sent as `Authorization: Bearer <jwt>`. `ollie-mcp` verifies locally via JWKS (cached 1 h; emergency-invalidate via Redis pub/sub — see §2.10.2). |
| **API key** | CI, scripted use, headless Claude Code, self-hosted | Accept either `Authorization: Bearer <opik-api-key>` (MCP convention) or `Authorization: <opik-api-key>` raw (Opik convention today). `ollie-mcp` strips a `Bearer ` prefix if present, then validates via existing `RemoteAuthService` round-trip to the Comet React service. |
| **Self-hosted local** | OSS Opik deployments | API-key path only out of the box; optional OAuth via operator-configured IdP. |

**Workspace scoping: token-scoped (Model A).** Each OAuth grant is bound to one workspace, chosen at the consent screen. Multi-workspace users add one connector per workspace (each named "Opik — &lt;workspace&gt;"). This matches OAuth idiom and doesn't require host UIs we don't control to expose a workspace picker. Per-token revocation happens at the OAuth layer (`/oauth/revoke`); there is no customer-facing revocation UI at launch (see ADR 0006).

**Granular scopes ship day one** (no coarse-`mcp:write` first):

| Scope | Required for |
|---|---|
| `mcp:read` | `read` and `list` tool calls. |
| `mcp:write:traces` | `create_trace`, `create_span`. |
| `mcp:write:annotations` | `score`, `comment`. |
| `mcp:write:test_suites` | `add_test_suite_items`, `save_eval_item`. |
| `mcp:write:prompts` | `save_prompt_version`. |
| `mcp:write:experiments` | `run_experiment` write side effects. |
| `mcp:ask_ollie` | `ask_ollie`. |
| `mcp:run_experiment` | `run_experiment` invocation. |

Granular scopes mean (a) the consent screen shows users exactly what they're approving, (b) users granting consent can decline a single tool family (e.g., uncheck `mcp:ask_ollie` for compliance), (c) no follow-on migration to split scopes later. Per-workspace admin-driven scope disable is not part of the launch surface (see ADR 0006); enforcement at launch is token-level via the granted-scope set on each OAuth grant.

## 1.9 Quotas & cost model

`ask_ollie` and `run_experiment` consume Anthropic API tokens (Ollie) or pod compute. Quotas exist primarily as cost control, secondarily as abuse protection. Direct writes are cheap REST and don't meter against tier caps.

**Definition: one `ask_ollie` call** = one successful `tools/call` invocation with `name="ask_ollie"`. Failed calls that never reach an LLM (auth/scope/quota errors) don't count. Failures *after* Ollie started reasoning **do** count — the cost has already been incurred. Internal Anthropic requests Ollie makes inside a single call (sub-agents, retries, planning) are not exposed.

| Tier | `ask_ollie`/day | `run_experiment`/day | Direct writes | Notes |
|---|---|---|---|---|
| **Free (cloud)** | 20 | 5 | unlimited | Hard cap. 429 with `Retry-After` and an upgrade URL. |
| **Pro (cloud)** | 500 | 50 | unlimited | Soft cap; overage emailed, surfaced in billing UI. |
| **Enterprise (cloud)** | Contract-bound | Contract-bound | unlimited | Per-workspace contractual cap. |
| **Self-hosted** | Operator's choice (default 0 = disabled) | Operator's choice (default 0 = disabled) | unlimited | Their Anthropic bill, their rules. Defaults to **0** so a fresh self-hosted install does not silently start spending — see §2.10.2 "Self-hosted Anthropic quota". |

**Soft-warning surfacing.** Every `tools/call` response that succeeds against a quota-tracked tool returns `metadata.quota_warning` once usage ≥ 80% of cap (per §2.13 error taxonomy), with `{used, cap, remaining, reset_at}`. Hosts can show "you've used 17/20 ask_ollie calls today" inline. The hard 429 still fires when the cap is reached.

Operational caps (independent of tier): 100 req/min per token for DoS protection, 10 MB response cap.

**Migration grace** for workspaces moving from `opik-mcp` to the hosted product: 2× tier baseline for 90 days. Detection: `opik-backend`'s request logger already records `User-Agent`; a daily-aggregated `mcp_legacy_activity` view (`workspace_id, last_seen_at`) is populated from rows whose `User-Agent` matches `opik-mcp/.*`. On first hosted-MCP OAuth grant or first API-key call, `ollie-mcp` consults this view; if `last_seen_at` is within 30 days, `mcp:quota_modifier:{workspace_id}:migration_grace_until = now() + 90 days` is written as a Redis key (TTL 90 days) and the quota-policy module multiplies caps by 2 while the key is live. (Originally specified on `mcp_workspace_settings`; relocated to Redis because that table is deferred — see ADR 0006.)

**First-connect trial bonus** (free tier only): the first 7 days after first OAuth grant or first API-key call, free tier gets 2× `ask_ollie` cap (40/day). Stored as `mcp:quota_modifier:{workspace_id}:trial_bonus_until` (Redis, TTL 7 days). Eliminates the "evaluated MCP for 30 minutes, hit the cap, left" failure mode. Trial bonus and migration grace stack only up to a hard ceiling of 2× — they don't compound.

## 1.10 Self-hosted story

Self-hosted Opik (docker-compose / helm) today bundles `opik-backend`, `opik-frontend`, ClickHouse, MySQL — **not Ollie**. At launch:

- **`ollie-mcp`** ships in the bundle as an always-warm service. Direct-write tools, resources, and (operator-configured) OAuth all work out of the box. API-key path is the default.
- **`ollie-assist`** ships **conditionally** — the operator opts in by providing `ANTHROPIC_API_KEY`. The Anthropic SDK in `ollie-assist/src/ollie_assist/app.py:49` (`AsyncAnthropic(api_key=settings.anthropic_api_key)`) is unconditional, so without a key the bundle skips the Ollie image entirely and `ollie-mcp` omits `ask_ollie` / `run_experiment` from `tools/list`.
- **Pod provisioning in self-hosted.** A thin helm-based provisioner mirrors the codepanels orchestrator. For small deployments this is a single static pod (one shared `ollie-assist` for the whole installation).
- **OAuth issuer.** Cloud uses comet-backend's OAuth AS; self-hosted does not ship comet-backend. Operators wire OAuth into their own IdP via the same `/oauth/*` routes if they want browser MCP installs; the API-key path requires no setup.
- **MCP availability control** is deployment-wide on both cloud and self-hosted at launch (no per-workspace UI toggle — see ADR 0006). Self-hosted operators stop the `ollie-mcp` container to disable; cloud disables via global feature-flag in `comet-backend` (operator-managed, not customer-managed).

If self-hosted Opik is running unauthenticated (`OPIK_NO_AUTH=true`), `ollie-mcp` **refuses to start** unless `OPIK_MCP_ALLOW_UNAUTH=true` is set explicitly. Default-deny keeps accidentally-public deployments from exposing trace data over MCP.

## 1.11 `opik-mcp` (TS) — polished, not deprecated

The existing TS server at [comet-ml/opik-mcp](https://github.com/comet-ml/opik-mcp) is **not deprecated**. It's a different product with different strengths: zero-dependency local install, scriptable from CI, no LLM cost. We polish it as the **scripted / CI / no-LLM path** alongside the hosted product.

| Concern | Decision |
|---|---|
| Repo location | Stays in `comet-ml/opik-mcp` (TS, separate repo). |
| Tool surface | Audited against Opik 2.0 (§2.7). Broken tools fixed; surface remains the CRUD-per-endpoint shape it has today. |
| README | Repositions as "the scripted / CI / no-LLM path. For interactive AI host integration use `https://www.comet.com/api/v1/mcp`." |
| Maintenance | Same release cadence; no feature freeze. |

**The hosted MCP and `opik-mcp` are different shapes, not drop-in replacements.** Hosts that want one-click OAuth + Ollie use the hosted endpoint. Hosts that want offline CRUD use `opik-mcp`. The migration matrix in §2.7 walks every `opik-mcp` tool to its hosted equivalent (resource, direct tool, or `ask_ollie`).

## 1.12 Discovery surface

Activation funnel:

```
Saw MCP affordance → opened install URL → completed OAuth → first tools/call → first ask_ollie → returned ≥3 days later
```

Surfaces driving entry:

| Surface | Where |
|---|---|
| **In-Opik UI "Connect" banner** | Opik web UI top-level — explains MCP, copies URL, shows per-host instructions (Claude Code, Cursor, claude.ai). |
| **Anthropic Custom Connector directory listing** | `claude.ai` connector picker. Verified status via CIMD pre-registration. |
| **Cursor & VS Code Copilot directories** | Same CIMD entries. |
| **Docs hub** | `comet.com/docs/opik/mcp`. Setup per host, quotas, troubleshooting, data flow. |
| **MCP server registry listing** | `modelcontextprotocol.io` server registry. |
| **Launch blog post + lifecycle email** | Comet blog + email to existing Opik users. |
| **In-app changelog item** | Existing Opik what's-new. |

Funnel metrics tracked in **Amplitude** (Comet's existing product analytics). Events: `mcp_install_clicked`, `mcp_oauth_started`, `mcp_oauth_completed`, `mcp_tools_call_first`, `mcp_ask_ollie_first`, `mcp_returned_3d`. Events emit from both the in-Opik banner (`mcp_install_clicked`) and `ollie-mcp` (`mcp_tools_call_first` onwards) via a shared analytics client.

## 1.13 Admin & dashboards — deferred

The customer-facing admin/dashboard surface (workspace MCP settings tab, per-workspace toggles, per-scope disable, connected-clients list with revoke buttons, usage report UI, audit-export UI, retention picker) is **out of scope for the current launch**. See ADR 0006 for the deferral rationale.

What still ships:

- **Token revocation via OAuth `/oauth/revoke`** (RFC 7009) — programmatic only, no UI.
- **Audit-log writes** continue (structural — `mcp_audit_log` table in `comet-backend`) so we retain the data when we ship the UI later. Retention is a single global default (12 months); no per-workspace retention.
- **Operational SRE observability** (Prometheus metrics, internal Grafana, alerts, runbook) per §2.11 — needed to run the service in prod.
- **OAuth-scope-level controls** at consent time give users self-service ability to decline tool families (e.g., uncheck `mcp:ask_ollie`).

What is removed from launch:

- The `mcp_workspace_settings` table and all per-workspace flags (`mcp_enabled`, `ask_ollie_enabled`, `scope_disable`, `mcp_audit_retention_months`). Global behavior only.
- The `mcp_export_jobs` table and async audit export job. Audit data is queryable internally (SQL/SRE) until a UI ships.
- All `/api/admin/mcp/*` endpoints in `comet-backend`.
- The "MCP" tab in `opik-frontend` workspace settings.
- Customer-facing cost/usage dashboards (operator dashboards in internal Grafana remain).
- Workstream W6 (admin & audit UI) — removed from §2.18.

This is launch scoping, not a permanent decision. Enterprise customers who need revocation UI / per-workspace toggles are tracked as a follow-up.

## 1.14 Launch checklist

What must be ready at GA:

- [ ] All 10 workstreams (§2.18) signed off.
- [ ] Blog post drafted, scheduled.
- [ ] Docs hub page live on `comet.com/docs/opik/mcp`.
- [ ] In-Opik UI "Connect" banner shipped behind feature flag.
- [ ] Anthropic / Cursor / Microsoft CIMD pre-registration entries published and verified.
- [ ] `modelcontextprotocol.io` server registry listing submitted.
- [ ] Customer-success comms to top-50 Opik power users.
- [ ] Lifecycle email scheduled to all Opik users.
- [ ] Status page entry + Prometheus alerts wired (§2.11).
- [ ] DPA addendum drafted with legal covering Anthropic data flow.
- [ ] Runbook published at `docs/runbooks/ollie-mcp.md`.
- [ ] Manual host-matrix smoke (claude.ai, Claude Code, Cursor, VS Code Copilot) all green.

## 1.15 Out of scope

- A generic MCP gateway / multi-tenant MCP hosting platform.
- DXT/`.mcpb` packaging (revisit post-launch if demand exists).
- Migrating `opik-mcp` (TS) to Python — different product, stays as TS.
- Building a `comet-cli` or `opik-cli`.
- A2A (agent-to-agent) protocol support.
- Internationalizing the consent screen (English at launch; i18n fast-follow).
- **Code-mode tool (`execute_python`) — intentionally out of scope.** Ollie already ships a sandboxed-subprocess Python executor (`ollie-assist/src/ollie_assist/tools/opik_sdk.py`) that runs user-supplied code against the Opik SDK with the user's API key, blocking DELETE at the network layer. Anthropic's published guidance on tool-bloat ([Anthropic — Code execution with MCP]) explicitly recommends *"10–15 outcome-oriented tools + code mode for the long tail"* — `opik_sdk` is the code-mode escape hatch for the long tail our nine deterministic tools don't cover ("delete every trace older than 90 days in project foo"). Exposing it via MCP requires: (a) a dedicated `mcp:execute_python` scope shown distinctly on the consent screen and disable-able per-workspace, (b) plan gating (Pro+ on cloud; operator opt-in on self-hosted), (c) sandboxing extended from per-user-pod to the `ollie-mcp` boundary so code runs against the user's minted key without ever touching the cluster service account, (d) a separate quota counter independent of `ask_ollie`. Not on the W1–W10 list.

## 1.16 Why these choices — evidence

Each decision is backed by external data, MCP-spec direction, or a code path we read in our own repos.

### D1. Eleven outcome-oriented tools, not thirty
- Anthropic's published guidance (Feb 2026 Tool Search + Programmatic Tool Calling): **"10–15 outcome-oriented tools + code mode for the long tail"** [Anthropic — Code execution with MCP]. Eleven sits within that band.
- MCP-Universe benchmark (Salesforce AI Research, 2025): the best frontier model scores **43.72% overall** on 231 tasks across 11 MCP servers — concrete evidence that surface size and shape matter, not just count.
- Anthropic's own threshold: **"Claude's ability to correctly pick the right tool degrades significantly once you exceed 30–50 available tools"** [Anthropic — Tool use best practices]. We're well below that.
- Anthropic Tool Search data: Opus 4.5 jumps **79.5% → 88.1%** with Tool Search enabled — the lever for scaling tool surfaces is search-over-narrow, not polymorphism.
- Today's `opik-mcp` with `OPIK_TOOLSETS=all` exposes well over 30 tools — pushes against that threshold; the new surface stays well under it.

### D2. Ollie as the doorway, not raw primitives
- Ollie already has session memory, sub-agents, compaction, confirm gates, navigate emission, test-suite writes, eval runs, skill loading — see `ollie-assist/src/ollie_assist/{agents,tools}/`. Re-exposing those as MCP primitives is multi-quarter rebuild work.
- Notion, Linear, Atlassian Rovo — all hosted MCPs expose **narrow outcome-oriented surfaces**, not CRUD-per-endpoint.
- Generic host LLMs don't know Opik 2.0's info architecture. Putting the domain expertise in `ask_ollie` keeps planning where the domain knowledge lives.

### D3. Reads via universal tools, not Resources
- The MCP spec rhetorical guidance ("Resources for reading, Tools for doing") **predates the resources-invisibility problem**. In practice: Claude Code doesn't surface MCP resources to the agent at all, and Cursor only renders them as `@`-mention completions when the user explicitly types `@opik://...` (verified per ADR 0004 §"Empirical inputs §4").
- ~95% of mid-session usage is reads (§1.3 JTBD analysis). A read primitive invisible to the primary host bottoms out the value prop, so we surface reads as universal tools (`read` / `list`) mirroring ollie-assist's `ENTITY_REGISTRY` 1:1.
- Trade-off: lose the `@opik://traces/<uuid>` mention completion in Cursor (a minor UX regression for one client). `opik://` URIs are still accepted as `id` input to `read` for forward-compat with any future resource-aware host. Full rationale: [ADR 0004 D1](decisions/0004-tool-surface.md#d1-reads--universal-tools-move-from-resources-to-tools).

### D4. Streamable HTTP only on the hosted path
- MCP 2026 roadmap names Streamable HTTP as the production transport and states the working group **will not introduce additional official transports this cycle** [MCP roadmap].
- HTTP+SSE is being phased out across the ecosystem.

### D5. Endpoint on `comet.com` (path-based)
- `sessionToken` cookie is set by `comet.com` (per `AuthFilter.java`, `RemoteAuthService.java`). Browsers only send it to `comet.com` hosts. A subdomain `mcp.comet.com` forces re-login at authorize.
- Path-based reuses existing edge, TLS, WAF, rate-limits — near-zero operational cost.

### D6. Dedicated `ollie-mcp` repo + image
- The hosted MCP server **cannot live inside `ollie-assist`**: `ollie-assist` runs one pod per workspace, scaled from zero — no stable external URL, up to 2 min cold start, no JWT verifier. Hosts register one URL and time out at ~30 s.
- The hosted MCP server also **does not belong inside `opik-backend`**: it's a stateful streaming service with its own deploy cadence, language (Python, matching Ollie's stack), and on-call posture.
- A dedicated repo gets us: independent release cycle, focused CI, clear ownership boundary, no risk of taking `opik-backend` offline when we ship MCP changes.
- The dedicated image `ghcr.io/comet-ml/ollie-mcp` ships always-warm at the comet gateway tier (≥2 replicas).

### D7. OAuth 2.1 + PKCE + DCR + pre-registered + CIMD
- MCP spec mandates OAuth 2.1 for remote servers.
- Anthropic's claude-code has documented gaps when only DCR is supported — Azure AD / Entra ID and other enterprise IdPs don't support DCR [issues #52638, #38102, #26675, #53253].
- Client ID Metadata Documents (Nov 2025, SEP-991) are the recommended modern alternative.

### D8. RS256 from day one
- HS256 means every verifier holds the shared secret; cross-region, cross-pod, and the upcoming Anthropic verified-connector posture all assume local verification with public keys.
- RS256 + JWKS published at `/.well-known/jwks.json` lets `ollie-mcp` verify in-process without a comet-backend round-trip, lets pod-side nginx verify the service-account JWT, and lets multi-region deploys verify without cross-region calls.
- Migrating from HS256 later is a coordinated dual-write rollout across three services; doing it on day one is one signing path and one JWKS endpoint.

### D9. Granular scopes from day one
- The Anthropic verified-connector posture and enterprise IT consent expectations both presuppose tool-family-level granularity.
- Coarse `mcp:write` would require a re-consent migration to split later (every existing token forced through OAuth again).
- The consent screen renders scopes today; any future admin UI (deferred per ADR 0006) will render the same scopes, so one decision tree carries forward.

### D10. Self-hosted = API-key by default, OAuth optional
- Self-hosted Opik already issues API keys via UI.
- Self-hosted operators with an IdP (Keycloak, Auth0, Okta) can wire OAuth into the same `/oauth/*` routes if they want browser installs.

### D11. `opik-mcp` (TS) polished, not deprecated
- Different product, different shape. CRUD per endpoint, no LLM, zero install dependency on Comet infra.
- Audit + README change are cheap; carrying it forward preserves a real use case (CI, scripted, offline).

### D12. Token-scoped workspace (Model A)
- Matches OAuth idiom (one grant, one resource scope).
- Per-workspace revocation is one OAuth `/oauth/revoke` call (per-workspace UI deferred — see ADR 0006).
- Host UIs (claude.ai, Cursor, Claude Code) don't expose workspace pickers we'd need for per-call selection. Users can name connectors per workspace and switch the same way they switch any other tool.

### D13. Quota policy
- `ask_ollie` is the cost driver. Free-tier evaluation needs enough headroom for a "wow" but a hard daily cap on Anthropic spend.
- Pro plan covers a power user comfortably (~25 sessions/day with multi-turn).
- Enterprise contract-bound because predictability matters more than caps for that segment.

### D14. Admin/dashboard UI deferred (superseded)
- Original position: ship at GA as an enterprise unlock.
- Revised position (ADR 0006): defer to a follow-up. Token revocation via `/oauth/revoke` (RFC 7009) covers the only hard launch dependency; audit data is still captured server-side for when the UI ships. Per-workspace toggles, retention picker, usage report UI, audit export UI all removed from launch.

## 1.17 Decisions still open for the meeting

Most decisions are made (§1.16 + the choices folded into §§1.3–1.13). The items genuinely benefiting from broader discussion are mirrored in the brief — repeated here for completeness:

1. **`save_eval_item` vs `add_test_suite_items` — one tool or two?** Both write rows to the same test-suite entity (Opik 2.0 retired generic datasets, so there is no longer a "different upstream" argument), but parameter shapes differ — bulk-with-`source` vs single-with-`target`. Tool-count discipline argues for one polymorphic tool with a `mode` discriminator; clarity to the host LLM argues for two clearly named tools. Current proposal: keep two.
2. **CIMD pre-registration list day-one.** Anthropic, Cursor, Microsoft are committed. Continue.dev / Cline / Zed?
3. **Self-hosted Anthropic-key UX.** First-run wizard prompt vs. docs-only?
4. ~~**Pod readiness signaling.** Orchestrator-pushed webhook on pod ready, or `/health/ready` polling? Pull is simpler; push is faster on a fully-cold pod.~~ **Closed:** `/health/ready` polling, 1 s interval, 2-min cap (§2.5, W5). Push requires the orchestrator to know `ollie-mcp`'s callback URL per region and adds a fan-out fail-mode (drop the webhook, hang the host). Pull keeps the failure local to the polling replica. Re-open only if cold-start p99 exceeds 90 s sustained for 2 weeks.
5. **Pod-side JWT verifier location.** nginx Lua (in-band, no extra process) vs. small Python sidecar (more conventional, more memory)? Current proposal: nginx Lua.
6. **EU data residency for `ask_ollie`.** US Anthropic region only at launch with EU customers warned, or block EU customers until EU Anthropic region is wired?

---

# Part 2 — Engineering

## 2.1 Architecture

```
                                  ┌───────────────────────────────────────┐
External MCP host ──MCP HTTP─────▶│  ollie-mcp  (NEW repo, NEW image)     │
(Claude Code, Cursor,             │  Python 3.13, FastAPI, sse-starlette  │
 claude.ai, VS Code Copilot)      │  Always warm, ≥2 replicas, Redis      │
                                  │  ─────────────────────────────────── │
                                  │  Public:                              │
                                  │   /api/v1/mcp                         │
                                  │   (OAuth lives on comet-backend)      │
                                  │  Inside:                              │
                                  │   RS256/JWKS verifier (1h cache + inv)│
                                  │   Redis session map + SSE event log   │
                                  │   MCP Tasks engine                    │
                                  │   Tool dispatcher (pod vs REST)       │
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
        │   key (mTLS)     │
        │ Service-account  │
        │   JWT issuer     │
        └────────┬─────────┘
                 │ provisions (via codepanels)
                 ▼
        ┌──────────────────────────┐
        │ ollie-assist pod         │
        │ (one per workspace)      │
        │ ──────────────────────── │
        │ /sessions API + SSE      │
        │ + NEW: pod-side JWT      │
        │   verifier (nginx+JWKS)  │
        │ + NEW: per-session       │
        │   X-Opik-User-API-Key    │
        └──────────────────────────┘
```

**`ollie-mcp` is the only new long-running service.** Python FastAPI (matches Ollie's stack; shared auth + entity-fetch utilities). State:

- **Redis session map** — `mcp:session:{id}` → `{user_id, workspace_id, scopes, jwt_token_id, created_at}`. TTL = session lifetime.
- **Redis event log** — `mcp:events:{session_id}` → ordered list of SSE/notification events with monotonic `seq`. TTL = pod idle TTL (30 min). Used for `Last-Event-ID` resumption.
- **Redis user-key cache** — `mcp:mint:{jwt_token_id}:{workspace_id}` → minted Opik API key. TTL = key lifetime − 5 min.
- **Redis revocation bloom** — `mcp:jwt-revoked:{jti}` so verifiers reject revoked tokens within 60 s of revocation. Sub-second access.
- **Redis quota counters** — `mcp:quota:{workspace_id}:{tool}:{YYYY-MM-DD}` integer counters.

There is **no in-process session state**. `ollie-mcp` is stateless across replicas from the first commit; rolling deploys never invalidate sessions.

## 2.2 Repo-by-repo deltas

### 2.2.1 `comet-backend` (Java, Dropwizard)

New module: `comet-rest/.../oauth/mcp/`. The edge on `www.comet.com` maps `/oauth/*` and `/.well-known/*` to comet-backend.

- **`/oauth/authorize` (GET)** — reads `sessionToken` cookie via existing `TokenHelper`. If absent/expired, 302 to `/login?return_to=…`. If present, renders MCP consent template (see §2.3 + §2.10).
- **`/oauth/authorize` (POST)** — consent submission. Issues one-time code; stores `{client_id, user_id, workspace_id, scopes, code_challenge}`; redirects with `?code=…&state=…`. CSRF protected via existing Comet CSRF infrastructure.
- **`/oauth/token` (POST)** — code → access_token (**RS256 JWT**, 1 h, signed with the current key) + refresh_token (opaque, 30 d, stored in `mcp_refresh_tokens`). PKCE `code_verifier`. Refresh-token rotation on every refresh.
- **`/oauth/register` (POST)** — DCR per RFC 7591. Rate-limited primarily on `software_id`: **200/day per `software_id`**, plus a per-IP backstop of **50/hour per IP**. Anonymous registrations (no `software_id`) get the per-IP limit AND a platform-wide cap of **200 anonymous registrations/day total**. CIMD and pre-registered clients are preferred for high-volume hosts and bypass these limits. All DCR-supplied fields (`client_name`, `client_uri`, `tos_uri`, `policy_uri`, `redirect_uris`, etc.) are HTML-escaped before any template render; registrations whose fields contain raw `<script>`, `javascript:`, or `data:` URLs are rejected with `invalid_client_metadata`.
- **`/oauth/revoke` (POST)** — RFC 7009. Accepts `token` + `token_type_hint`; marks the row revoked and pushes a revocation event to Redis (`mcp:jwt-revoked:{jti}`) so `ollie-mcp` evicts cached mint keys within 60 s.
- **`/oauth/mint-user-api-key` (POST)** — issues a short-lived Opik API key scoped to `(user_id, workspace_id)`, valid 1 h. **Network isolation:** k8s NetworkPolicy restricts ingress to the `ollie-mcp` ServiceAccount only. **Auth:** mTLS client cert + a service-account RS256 JWT in the `Authorization` header (`aud=comet-backend`, `iss=comet-mcp`, `sub=ollie-mcp-service`). **Per-target rate limit:** 60 mints/hour per `(user_id, workspace_id)`. Every mint (success or failure) writes one row to `mcp_mint_audit`. **Wire shape**: `Request: {"user_id": "<uuid>", "workspace_id": "<uuid>", "scopes": ["mcp:write:traces", ...], "ttl_seconds": 3600}`. `Response 200: {"api_key": "<opaque-string>", "expires_at": "<rfc3339>"}`. Errors: `400 invalid_request`, `403 forbidden` (workspace mismatch / scope not allowed), `429 rate_limited` (with `Retry-After` header), `503 service_unavailable` (DB outage — see "Fail-closed behavior" below). **Fail-closed behavior**: on DB unavailability or 5xx from this endpoint when the Redis mint cache misses, `ollie-mcp` returns `tools/call` error `mint_unavailable` (HTTP 503 with `Retry-After: 30`) — no degraded mode. The mint cache TTL is set to `key_lifetime − 5 min` and the cache entry is proactively refreshed at `−10 min` so a brief outage during the refresh window doesn't fail in-flight sessions.
- **`/api/opik/ollie/compute` (GET, extended)** — today returns `{computeURL, enabled}` for browser callers presenting a Comet session cookie. Extended to accept service-account JWT callers (`ollie-mcp`) and return the same payload for a `(user_id, workspace_id)` resolved from the JWT subject. The pod-provisioning side effect (codepanels) is unchanged.
- **`/.well-known/oauth-authorization-server`** — RFC 8414. Lists supported scopes (granular set from §1.8), grant types, PKCE methods.
- **`/.well-known/oauth-protected-resource`** AND **`/.well-known/oauth-protected-resource/api/v1/mcp`** — RFC 9728 (resource server metadata; lists `https://www.comet.com/api/v1/mcp` as the resource). Per MCP 2025-11-25 auth, clients probe the path-qualified form first then fall back to root; serve both (one redirects to the other or both return identical metadata).
- **`/.well-known/jwks.json`** — public keys for the RS256 signer. Two keys live at any time (current + previous) so verifiers tolerate the rotation window. Rotation cadence: monthly, automated. **Response headers:** `Content-Type: application/json`, `Cache-Control: max-age=3600, stale-while-revalidate=300`, plus a strong `ETag` derived from `kid` set. The JWKS document is cached server-side in a `mcp_jwks_cache` table (or in-memory with a Liquibase changeset that seeds initial keys) so a hot-path key-rotation event doesn't require a DB query on every verifier fetch.

Reuses existing `TokenService`, `TokenHelper`, `TokenScope`. New scopes: `mcp:read`, `mcp:write:traces`, `mcp:write:annotations`, `mcp:write:test_suites`, `mcp:write:prompts`, `mcp:write:experiments`, `mcp:ask_ollie`, `mcp:run_experiment`.

**New DB tables (comet-mysql migration; tool: Liquibase — same as `opik-backend`).**

Migration is split across multiple changesets so DDL doesn't lock: (1) `CREATE TABLE ...` per table with `runOnChange: false`; (2) seed `mcp_jwks_cache` with initial key pair. Every changeset ships a matching `<rollback>` block (partitioned-table rollback uses `DROP TABLE`, not `ALTER TABLE REMOVE PARTITIONING`).

Per ADR 0006, the customer-facing admin/dashboard surface is deferred — the previously specified `mcp_workspace_settings` and `mcp_export_jobs` tables and the `/api/admin/mcp/*` endpoint family are not part of launch. Audit log writing is retained; export-to-S3 and per-workspace retention picker are not.

- `mcp_oauth_clients` (client_id CHAR(36) PK, name VARCHAR(255), redirect_uris JSON, is_dcr BOOL, is_cimd BOOL, cimd_url VARCHAR(2048) NULL, owner_user_id CHAR(36) NULL, rotated_from_client_id CHAR(36) NULL, software_id VARCHAR(255) NULL, created_at TIMESTAMP(6), revoked_at TIMESTAMP(6) NULL). The non-NULL uniqueness constraint on `cimd_url` is implemented as a functional unique index because MySQL does not support partial indexes on InnoDB natively: `UNIQUE INDEX idx_cimd_url_nonnull ((CASE WHEN cimd_url IS NULL THEN NULL ELSE cimd_url END))`. The Java service layer additionally checks for an existing non-NULL row before insert as a defense-in-depth measure.
- `mcp_authorization_codes` (code_hash CHAR(64) PK, client_id, user_id, workspace_id, code_challenge VARCHAR(128), code_challenge_method ENUM('S256'), scopes JSON, redirect_uri VARCHAR(2048), expires_at TIMESTAMP(6), used_at TIMESTAMP(6) NULL, INDEX idx_expires_at (expires_at)) — short-lived (60 s); the raw code is stored as `code_hash = sha256(code)` to prevent DB read from yielding usable codes. **The token endpoint MUST verify `expires_at > NOW(6) AND used_at IS NULL` on every code lookup** — sweeper is a defense-in-depth scrubber, not the authoritative expiry mechanism. After successful exchange the endpoint sets `used_at = NOW(6)` in the same transaction.
- `mcp_refresh_tokens` (token_hash CHAR(64) PK, client_id, user_id, workspace_id, scopes JSON, jti CHAR(36), `family_id` CHAR(36) NOT NULL, rotated_from CHAR(36) NULL, issued_at TIMESTAMP(6), expires_at TIMESTAMP(6), rotated_at TIMESTAMP(6) NULL, revoked_at TIMESTAMP(6) NULL, revoked_reason VARCHAR(64) NULL, INDEX idx_family_id (family_id, revoked_at)). `family_id` is the `jti` of the initial grant and is propagated to every rotation descendant, so reuse-detection is a single indexed lookup (`SELECT revoked_at FROM mcp_refresh_tokens WHERE family_id = ? AND revoked_at IS NOT NULL LIMIT 1`). On reuse-after-rotation detection the **entire family** is revoked — but "family" is precisely the chain from one initial grant, so the blast radius is one user-client pair, never an entire CIMD-registered host's userbase.
- `mcp_audit_log` (id CHAR(26) NOT NULL, ts TIMESTAMP(6) NOT NULL, workspace_id CHAR(36) NOT NULL, user_id CHAR(36), token_id CHAR(43), tool VARCHAR(64), thread_id CHAR(36) NULL, success BOOL, status_code SMALLINT, latency_ms INT, anthropic_cost_units INT NULL, request_size INT, response_size INT, prev_hash CHAR(64) NULL, row_hash CHAR(64) NOT NULL, **PRIMARY KEY (workspace_id, ts, id)**). `id` is an **application-generated ULID** (lexicographically sortable, microsecond unique per emitter) — not `AUTO_INCREMENT`, because composite-PK partitioned tables don't permit auto-increment on a non-leading column. Writes use `INSERT IGNORE INTO mcp_audit_log ...` so at-least-once retries from the `mcp:audit-buffer` Redis list are idempotent. **Tamper-evidence**: each row stores `row_hash = sha256(prev_hash || canonical_json(row))` and a daily worker exports the day's partition + a Merkle root to a WORM (object-lock) S3 bucket (`s3://comet-mcp-audit-archive`, retention = max workspace retention setting, immutable). Auditors verify the chain by re-computing hashes from the archive. Partitioning: `PARTITION BY RANGE (UNIX_TIMESTAMP(ts))` with monthly partitions (NOT `TO_DAYS(ts)` — that function is incompatible with `TIMESTAMP` partitioning in MySQL 8 and raises ERROR 1486). Example partition definitions:
  ```sql
  PARTITION BY RANGE (UNIX_TIMESTAMP(ts)) (
    PARTITION p202601 VALUES LESS THAN (UNIX_TIMESTAMP('2026-02-01 00:00:00')),
    PARTITION p202602 VALUES LESS THAN (UNIX_TIMESTAMP('2026-03-01 00:00:00')),
    -- ...
    PARTITION p_future VALUES LESS THAN MAXVALUE
  );
  ```
  **Retention strategy** (global, ADR 0006): a single retention window applies to the whole table — **12 months** at launch. The daily pruner runs one pass: `ALTER TABLE ... DROP PARTITION p202xMM` for any partition whose end is older than 12 months. No per-workspace retention picker; no row-level pruner pass. Retention is operator-tunable via a single config value, not a customer-controlled UI.
- `mcp_mint_audit` (id CHAR(26), ts TIMESTAMP(6), user_id, workspace_id, minted_key_id CHAR(43), requesting_pod VARCHAR(255), jwt_token_id CHAR(43) NULL, success BOOL, **PRIMARY KEY (workspace_id, ts, id)**) — partitioned by `RANGE (UNIX_TIMESTAMP(ts))` monthly; ULID `id`; same `INSERT IGNORE` semantics. Tamper-evidence chaining not required (this table is a write-once forensic record).
- `mcp_jwks_cache` (kid VARCHAR(64) PK, key_type ENUM('user','service'), public_jwk JSON, private_jwk_encrypted BLOB, created_at TIMESTAMP(6), retired_at TIMESTAMP(6) NULL). Seeded by the initial migration with the first key pairs. The `/.well-known/jwks.json` handler reads from here without DB write contention; the rotation cron updates two rows atomically.

All timestamp columns use `TIMESTAMP(6)` (microsecond precision, UTC) — never `DATETIME` — so range queries, retention pruning, and audit-export windows survive timezone-boundary edge cases and partition pruning works on a monotonic value.

**Auth contract changes (extending existing code).** `TokenService` and `TokenHelper` (current cookie-session implementations) are extended with a new `MCPTokenStrategy` class that produces RS256 JWTs (existing strategies remain unchanged); new scope constants extend the existing `TokenScope` enum. The consent screen template is rendered with **Freemarker** (same engine the existing Comet login pages use); CSRF protection reuses the existing double-submit-cookie pattern with `workspace_id` included in the CSRF-protected form payload so a GET/POST workspace mismatch fails validation. (The previously specified `planTier` extension to `RemoteAuthService.AuthResponse` is no longer needed — it was driven by the deferred admin retention picker, ADR 0006.)

**Audit log row schema (wire format for `POST /internal/mcp/audit`).**
```json
{
  "rows": [
    {
      "id": "01HVZ...",                      // ULID, ollie-mcp generated
      "ts": "2026-05-12T10:23:45.123456Z",   // RFC3339 microseconds
      "workspace_id": "<uuid>",
      "user_id": "<uuid>",
      "token_id": "<sha256-prefix-43char>",  // never the raw JWT
      "tool": "ask_ollie",
      "thread_id": "<uuid>|null",
      "success": true,
      "status_code": 200,
      "latency_ms": 4231,
      "anthropic_cost_units": 1843,           // omit on non-ask_ollie calls
      "request_size": 412,
      "response_size": 28734
    }
  ]
}
```
The `prev_hash` / `row_hash` chaining columns are computed by `comet-backend` on insert from the previous row in the partition; the writer (ollie-mcp) does not see them.

**Service-account JWT issuer.** Mint short-lived (5 min) RS256 JWTs that `ollie-mcp` uses to authenticate to comet-backend (`/ollie/compute`, `/oauth/mint-user-api-key`, `/internal/mcp/audit`) and to per-user pods (`/sessions`). Signed with a **separate** RS256 key pair from the user-token signer (per §2.10.2), different `iss` (`comet-mcp`), different `aud` (`comet-backend` or `pod`).

**Audit-log write channel.** `POST /internal/mcp/audit` (mTLS-only, ServiceAccount-pinned to `ollie-mcp`; same NetworkPolicy as `/oauth/mint-user-api-key`). Accepts `{rows: AuditLogRow[]}` batches (up to 100 rows per call). `ollie-mcp` buffers up to 1 s or 100 rows, whichever first, then flushes. On comet-backend unavailability, `ollie-mcp` persists the batch to a local Redis list (`mcp:audit-buffer`) and retries with exponential backoff; the audit pipeline is **at-least-once**, dedup is by `(workspace_id, ts, id)`.

**Background workers.** Implemented as Dropwizard managed scheduled executors (the existing Dropwizard `LifecycleEnvironment.scheduledExecutorService` pattern already used elsewhere in comet-backend; not the third-party `@Scheduled` annotation, which is Spring). Multi-replica concurrency safety: each task acquires an advisory lock via `SELECT GET_LOCK('mcp_<task>', 0)` so only one replica runs a given iteration; failure to acquire the lock is a no-op for that tick. **Caveat — HikariCP & advisory locks**: MySQL `GET_LOCK` is connection-scoped. If the JVM crashes mid-task, the lock holds until the underlying TCP connection times out (bounded by HikariCP `maxLifetime` + MySQL `wait_timeout`, ~10 min default). To bound this window, each lock-holding task uses a dedicated single-connection `DataSource` (separate HikariCP pool of size 1) with `keepaliveTime=30000` and `maxLifetime=60000`, plus an idempotent design so a delayed re-run is safe.
- **Daily** `mcp_audit_log` retention pruner — drops monthly partitions older than the global 12-month retention.
- **Daily** audit-archive worker exports the previous day's `mcp_audit_log` partition + Merkle root to `s3://comet-mcp-audit-archive` (S3 Object Lock enabled, WORM, retention 12 months).
- RS256 key rotation cron (monthly, staggered between user-key pair and service-key pair so they don't rotate the same week). On rotation, the worker (a) inserts the new key into `mcp_jwks_cache`, (b) retires the prior-previous key, (c) publishes `POST /internal/jwks/invalidate` via Redis pub/sub so verifiers refresh before the next token issuance.
- Stale `mcp_authorization_codes` sweep (codes > 5 min old). This is a scrubber only; the token endpoint enforces expiry independently — see `mcp_authorization_codes` schema note.

**Admin endpoints:** none at launch (ADR 0006). Token revocation is via the standard OAuth `/oauth/revoke` (RFC 7009) above; there are no `/api/admin/mcp/*` endpoints in `comet-backend` for the current launch surface. Operator-side queries against `mcp_audit_log` are SQL-only until a UI ships.

### 2.2.2 `ollie-mcp` — new repo, new image

A dedicated repository at `comet-ml/ollie-mcp`, built as `ghcr.io/comet-ml/ollie-mcp:<spec-date>-<patch>`. Python FastAPI, Python 3.13.

**Why Python (vs. TypeScript / Go / Java).** The deciding factor is not raw HTTP throughput — the workload is I/O bound (downstream HTTP fan-out to opik-backend + SSE proxying from the per-user pod). The deciding factor is **code share with `ollie-assist`**: the MCP server's central job is translating Ollie's pod SSE event vocabulary (`thinking_delta`, `tool_call_*`, `confirm_required`, `navigate`, `compaction_*`, `message_end`) into MCP frames (`notifications/progress`, `notifications/tasks/updated`, `elicitation/create`). That vocabulary lives in `ollie-assist`'s Python types module. In Python the translator is `from ollie_assist.types.sse import SessionEvent` — one import, one source of truth, drift impossible. In any other language every change to Ollie's emitter would force a hand-translation in two repos.

Comparison table:

| Option | Strength | Weakness | Verdict |
|---|---|---|---|
| **Python 3.13 + FastAPI + `sse-starlette` + `mcp` SDK** (`modelcontextprotocol/python-sdk`, pin `^1.12`) | Shared code with `ollie-assist`: auth, async httpx client, SSE event types, Opik SDK usage. Same on-call rotation, same hire pool. First-party MCP SDK with built-in Streamable HTTP transport, OAuth resource-server helpers (`TokenVerifier`/`AuthSettings`), and **experimental Tasks support** (`server.experimental.enable_tasks()`, `ServerTaskContext.update_status`, `ToolExecution(taskSupport=TASK_REQUIRED)`). Async I/O comfortably handles thousands of concurrent SSE streams on commodity nodes. | ~120 MB RSS per replica vs. ~30 MB Go; 700 ms cold start. Irrelevant for an always-warm Deployment. | **Chosen.** |
| TypeScript + `@modelcontextprotocol/sdk` | Reference SDK most actively developed in TS; team has Node experience from `opik-mcp` (TS). | **Zero code share with `ollie-assist`** — every SSE event vocab change is a hand translation across two stacks. Two on-call languages. | Strong runner-up; would be the choice if `ollie-assist` were TS. |
| Go | Best raw HTTP throughput; tiny memory; single binary; great for many SSE streams. | No first-party MCP SDK (hand-roll JSON-RPC + Tasks primitive). No code share. Steepest learning curve for a Python+Java shop. | Premature optimization for an I/O-bound service. |
| Rust | Fastest, safest concurrency. | Team velocity drops 3-5× for the first quarter; community MCP libs immature. | Wrong project. |
| Java / Dropwizard (matches `opik-backend`) | Same stack and SRE patterns as `opik-backend`. | SSE / streaming in Dropwizard without Project Loom is awkward; per-connection thread costs hurt at high concurrency. No code share with Ollie. No mature MCP Java SDK. | Worst fit. |

Concrete code-share examples that drive the Python choice:
- `from ollie_assist.types.sse import SessionEvent, ThinkingDelta, ToolCallStart, ConfirmRequired, Navigate` — the SSE translator imports the exact event types the pod emits; type drift becomes a CI failure, not a runtime bug.
- `from ollie_assist.types.auth import WorkspaceContext` — the multi-tenant key handling work in W3 mirrors verbatim.
- The same `httpx.AsyncClient` factory used in `ollie-assist/src/ollie_assist/agent_core/context.py` for `get_or_create_user_opik_client(workspace)` is reused for the `read`/`list` tools and direct write tools.
- The `opik` Python SDK is the same dependency `ollie-assist` pins; bumping the Opik release version in both repos in lockstep is one PR per repo, not three.

```
ollie-mcp/
├── src/ollie_mcp/
│   ├── app.py                  # FastAPI app + Streamable HTTP router
│   ├── auth/
│   │   ├── bearer.py           # parse Authorization (Bearer/raw); routes to oauth.py / apikey.py
│   │   ├── oauth.py            # verifies RS256 JWT via JWKS (1h cache, refresh on kid miss, Redis pub/sub invalidation)
│   │   ├── apikey.py           # POSTs to React service /opik/auth (same path as opik-backend)
│   │   ├── service_jwt.py      # signs/verifies service-account JWTs (calls to comet-backend, pods)
│   │   ├── mint_client.py      # mTLS callout to comet-backend /oauth/mint-user-api-key
│   │   └── mint_cache.py       # Redis cache of minted user keys keyed by (jwt_token_id, workspace_id)
│   ├── transport/
│   │   ├── streamable_http.py  # MCP Streamable HTTP impl; Mcp-Session-Id; SSE upgrade; DELETE teardown
│   │   ├── session.py          # Redis-backed session store
│   │   ├── event_log.py        # Redis event log; monotonic seq; Last-Event-ID replay
│   │   └── tasks.py            # MCP Tasks primitive engine (create/get/cancel/events/result)
│   ├── tools/
│   │   ├── ask_ollie.py        # the doorway tool; resolves compute → opens pod /sessions
│   │   ├── create_trace.py     # POST /v1/private/traces
│   │   ├── create_span.py      # POST /v1/private/spans (append to existing trace)
│   │   ├── score.py            # POST /v1/private/{traces|spans|threads}/scores
│   │   ├── comment.py          # POST /v1/private/{traces|spans|threads}/comments
│   │   ├── add_test_suite_items.py        # POST /v1/private/test-suites/{id}/items (body: {items, source?})
│   │   ├── save_prompt_version.py      # POST /v1/private/prompts/versions
│   │   ├── run_experiment.py           # POST /v1/private/experiments + Tasks primitive
│   │   └── save_eval_item.py           # writes to test suite (type=evaluation_suite)
│   ├── resources/
│   │   ├── opik_uri.py         # opik:// URI parser
│   │   └── handlers.py         # one handler per URI template
│   ├── prompts/
│   │   ├── investigate_failure.py
│   │   └── compare_experiments.py
│   ├── compute/
│   │   ├── discovery.py        # calls comet-backend /api/opik/ollie/compute with service JWT
│   │   └── readiness.py        # /health/ready polling; 1s interval; 2-min cap
│   ├── quotas/
│   │   ├── tracker.py          # Redis counters; daily windows
│   │   └── policy.py           # tier → caps mapping
│   ├── observability/
│   │   ├── metrics.py          # Prometheus
│   │   └── logging.py          # structured logs, audit log writer
│   ├── opik_client.py          # async httpx client to opik-backend
│   └── pod_client.py           # async httpx + SSE client to ollie-assist pod
├── tests/
│   ├── unit/
│   ├── integration/            # in-process Ollie mock; mock OAuth issuer
│   └── conformance/            # MCP conformance harness
├── deploy/
│   ├── Dockerfile
│   ├── helm/                   # subchart for cloud deploy + self-hosted bundle
│   └── docker-compose.yaml     # local dev + self-hosted bundle entry
├── pyproject.toml
└── README.md
```

**Pinned dependencies (anchor on the SDK; don't hand-roll what's solved).**

| Concern | Library | Pin | Why this one |
|---|---|---|---|
| MCP protocol + transport | `mcp` (modelcontextprotocol/python-sdk) | `^1.12` | Built-in Streamable HTTP (`Server.streamable_http_app(streamable_http_path="/mcp", json_response=False, stateless_http=False)`), OAuth resource-server validation (`TokenVerifier` + `AuthSettings`), experimental Tasks engine (`server.experimental.enable_tasks()`, `ServerTaskContext.update_status`, `TASK_REQUIRED`). We use the low-level `Server` (not `FastMCP`) because we need to share the ASGI app with our own internal routes (`/internal/*` mTLS-gated) and to bind `Mcp-Session-Id` semantics ourselves. |
| Web framework | `fastapi` | `>=0.115` (matches ollie-assist) | Same stack as `ollie-assist`; SDK's ASGI app composes underneath. |
| SSE server | `sse-starlette` | `>=2.2` (matches ollie-assist) | Production-ready; multi-loop; graceful shutdown — the SDK uses it under the hood, we use it directly for the blocking-SSE fallback path. |
| SSE client (pod → MCP) | `httpx[http2]` + `httpx-sse` | `>=0.28` / `>=0.4` | `aconnect_sse` is the idiomatic upstream-SSE consumer; HTTP/2 keepalives reduce pod-side socket churn. |
| JWT/JWKS verification | `joserfc` | `>=1.0` | RFC-7515/7517/7519 compliant; `KeySet.import_key_set(jwks_json)` plus callable-key resolver handles our dual signing-key model (`user-<n>` / `svc-<n>` `kid` namespaces) and rotation without custom plumbing. Chosen over `PyJWT` for cleaner multi-kid KeySet ergonomics; chosen over `python-jose` (unmaintained). |
| Redis client | `redis[hiredis]` (redis-py) | `>=6.4` async | Session store, event log, mint cache, quota counters, thundering-herd warm-up lock (`asyncio.Lock` patterns + `redis.asyncio.lock.Lock`), JWKS emergency-invalidation pub/sub. |
| Opik typed REST client | `datamodel-code-generator` (build-time) + hand-thin `httpx.AsyncClient` wrapper | `>=0.26` | Generate Pydantic models from `opik-backend`'s OpenAPI spec at CI time → drop into `opik_client.py`. Avoid hand-typing dozens of DTOs and stay drift-free against Opik releases. (Considered `openapi-python-client`; rejected — generates a full client we'd have to override anyway for retry/auth.) |
| Opik SDK (for Tasks audit + traces from MCP itself) | `opik` | `==<release-tag>` (lockstep with `ollie-assist`) | Same dependency `ollie-assist` pins; bump in lockstep on every Opik release. |
| Structured logging | `structlog` | `>=25.0` | Context-var bound logger threads `trace_id`, `session_id`, `workspace_id`, `tenant_id` automatically; JSON renderer for Loki ingest. |
| Tracing | `opentelemetry-instrumentation-fastapi`, `-httpx`, `-redis` | latest stable | Auto-spans for inbound HTTP + downstream HTTP + Redis ops; exports OTLP to the existing Opik OTEL collector path. |
| Settings | `pydantic-settings` | `>=2.7` (matches ollie-assist) | Env-driven config parity with ollie-assist. |

**Dev / CI dependencies:**

| Concern | Library | Pin | Why |
|---|---|---|---|
| MCP host emulator | `@modelcontextprotocol/inspector` | latest | Hosted web UI to drive `tools/list`, `tools/call`, `resources/list`, Tasks lifecycle, elicitation — first stop for any developer touching the server; lives in `make inspect`. |
| Conformance harness | MCP SDK's `tests/conformance` fixtures | mirrored | Re-use the Python SDK's own conformance JSON fixtures rather than writing our own; track them via git submodule or vendored copy bumped on SDK release. |
| Mock HTTP | `respx` | `>=0.22` (matches ollie-assist) | Mock both opik-backend and pod traffic at httpx-transport layer. |
| Load | `k6` | latest | Same load harness as Opik SRE today. |

Estimated LoC: ~4–5k including tests. Coverage target **90%** (security-critical position).

### 2.2.3 `ollie-assist` — pod-side trust + multi-tenant key handling

The MCP server calls the per-user pod's existing `/sessions` + `/sessions/{id}/stream` + `/sessions/{id}/confirm`. Surfaces stay the same; the **authentication/identity model gets new capabilities** because Ollie today is single-tenant per pod (one env-supplied user Opik API key, cookie-based auth).

**Current state (pre-launch):**
- Auth: `get_current_user()` reads `sessionToken` from cookie and `comet-workspace` from header (`src/ollie_assist/routers/dependencies.py`). No server-side credential validation — Ollie trusts that comet-backend's session is being proxied to it (a static `BROWSER_AUTH` cookie value is injected at pod creation and the pod's nginx config matches it).
- Downstream Opik access: single shared `OLLIE_USER_OPIK_API_KEY` env var, consumed by `get_or_create_user_opik_client(workspace)` (per `CLAUDE.md` §"Opik API Keys"). Effectively single-tenant.

**New capabilities introduced for MCP:**

1. **Pod-side JWT verifier (NEW).** A new nginx Lua module (lua-resty-openidc or equivalent) validates `Authorization: Bearer <service JWT (RS256)>` against the `/.well-known/jwks.json` published by comet-backend (cached locally for 1 h to bound emergency revocation latency, refreshed on `kid` miss). The verifier checks `aud=pod`, `iss=comet-mcp`, signature, `exp`, and that `sub`/`workspace` claims match the pod's tenant. Successful verification injects `X-Internal-User-Id` and `X-Internal-Workspace-Id` headers downstream; failure returns 401 before the request reaches Python. This **replaces** the static `BROWSER_AUTH` cookie scheme for service-account callers (the cookie path remains for browser callers from the Comet UI).

2. **Per-session user Opik API key (NEW).** Accept `X-Opik-User-API-Key` header in `POST /sessions` and `POST /sessions/{id}/messages`. When present, the session-scoped client uses this key for that session instead of the env-supplied fallback. Implementation: thread the header through to a per-session override on `get_or_create_user_opik_client(workspace)`; when no header is present, fall back to the env key (preserves the today's single-tenant Comet-UI path). This makes Ollie **multi-tenant for MCP** — each MCP user gets their own Opik credentials per session.

3. **Streaming map (NEW for `ollie-mcp` side, not Ollie).** Ollie's existing SSE event vocabulary (`thinking_delta`, `message_delta`, `tool_call_*`, `compaction_*`, `confirm_required`, `navigate`, `error`, `message_end`) is translated to MCP frames by `ollie-mcp` per §2.5. Ollie's emitter does not change.

**No new Ollie endpoints. No new tools. No CLI.** The pod-side JWT verifier + per-session API key are additive; existing Comet-UI calls (cookie + env key) continue to work unchanged through a separate nginx `location` block.

Test surface required for the new capabilities:
- Service JWT valid → pod accepts; missing → 401; wrong `aud` → 401; expired → 401; `kid` not in JWKS → 401; stale JWKS triggers refresh and retry succeeds.
- `X-Opik-User-API-Key` present → key used in downstream Opik calls; absent → env fallback used.
- Cookie path still works for browser callers (regression).

### 2.2.4 `opik-backend` — unchanged

`ollie-mcp` calls existing REST endpoints. No code changes required. The OpenAPI schema is the source of truth (§2.14).

### 2.2.5 `opik-frontend` — no changes at launch

Per ADR 0006, the customer-facing MCP admin tab is deferred. No frontend changes are required for the current launch. The previously-specified components (connected-clients table, token-revocation modal, usage chart, audit-export polling, per-workspace + per-scope toggles, retention picker) and the `apps/opik-frontend/src/settings/mcp/` module are out of scope.

An in-Opik "Connect" install banner (§1.12) may still ship as a small, separate piece of frontend work — that is install UX, not admin UX, and is independent of this deferral.

### 2.2.6 `opik-mcp` (TS, separate repo) — polish, not deprecate

- Audit deliverable (§2.7) confirms `core` + `expert-*` toolset compatibility against Opik 2.0.
- README update repositioning as scripted / CI / no-LLM path.
- One-off fixes to any tools the audit shows broken (budget: ≤ 1 week of work).

## 2.3 OAuth flow (sequence)

```
Host                 comet.com                  ollie-mcp              opik-backend
 │                       │                          │                      │
 │ GET /.well-known/oauth-protected-resource         │                      │
 ├──────────────────────►│                          │                      │
 │ GET /.well-known/oauth-authorization-server        │                      │
 ├──────────────────────►│                          │                      │
 │ (optional CIMD: fetch host's metadata URL,         │                      │
 │  AS validates request signatures against JWKS)     │                      │
 │                       │                          │                      │
 │ GET /oauth/authorize? │                          │                      │
 │ client_id=…&          │                          │                      │
 │ redirect_uri=…&       │                          │                      │
 │ code_challenge=…&     │                          │                      │
 │ scope=mcp:read+       │                          │                      │
 │ mcp:write:traces+…&   │                          │                      │
 │ resource=…&state=…    │                          │                      │
 ├──────────────────────►│                          │                      │
 │  (cookie sessionToken)│                          │                      │
 │                       │ validate session         │                      │
 │                       │ render consent UI        │                      │
 │◄──────────────────────┤ HTML consent page        │                      │
 │ POST /oauth/authorize │                          │                      │
 │ (workspace_id, allow, │                          │                      │
 │  CSRF token)          │                          │                      │
 ├──────────────────────►│                          │                      │
 │                       │ verify CSRF, store code  │                      │
 │◄──────────────────────┤ 302 redirect_uri?code=…  │                      │
 │ POST /oauth/token     │                          │                      │
 │ code, code_verifier   │                          │                      │
 ├──────────────────────►│                          │                      │
 │                       │ verify PKCE              │                      │
 │                       │ sign RS256 JWT           │                      │
 │◄──────────────────────┤ {access, refresh, exp}   │                      │
 │                       │                          │                      │
 │ POST /api/v1/mcp      │                          │                      │
 │ Authorization: Bearer │─────────────────────────►│                      │
 │ initialize, tools/…   │                          │ verify JWT via JWKS  │
 │                       │                          │ resolve user+ws+scope│
 │                       │                          │ (first call:         │
 │                       │                          │  mTLS mint user key) │
 │                       │                          ├─────────────────────►│
 │                       │                          │ X-Opik-User-API-Key  │
 │                       │                          │ + Comet-Workspace    │
```

**Consent screen contents (§2.10 has security detail):**
- Comet logo + "Cursor wants to access your Opik workspace".
- **Workspace picker** — typeahead-searchable list with recently-used pinned to the top; plain dropdown for users in ≤ 5 workspaces. "default" filtered out per `RemoteAuthService` rule.
- **Granular scope list** (human-readable + scope strings):
  - ☐ Read traces, spans, test suites, prompts, experiments _(`mcp:read`)_
  - ☐ Write traces _(`mcp:write:traces`)_
  - ☐ Write annotations _(`mcp:write:annotations`)_
  - ☐ Write test-suite items _(`mcp:write:test_suites`)_
  - ☐ Write prompts _(`mcp:write:prompts`)_
  - ☐ Run experiments _(`mcp:write:experiments`, `mcp:run_experiment`)_
  - ☐ Ask Ollie (the AI assistant) _(`mcp:ask_ollie` — quota applies)_
- Expandable "What does Cursor get to see?" — links to data-handling docs.
- "Allow" / "Deny" — Deny returns to host with `error=access_denied`.
- Trust signals: Comet logo, verified-by-Anthropic badge once CIMD verification lands.
- All template-rendered fields HTML-escaped (workspace name, client name, redirect URI display, scope strings) — see §2.10.2.

## 2.4 Streamable HTTP details

- One endpoint: `POST,GET,DELETE /api/v1/mcp` (no separate `/cancel` endpoint — mid-request cancellation arrives as a JSON-RPC `notifications/cancelled` on the same `POST /api/v1/mcp` per MCP 2025-11-25 transport spec; `tasks/cancel` is the JSON-RPC method for Tasks-primitive cancellation and travels on the same endpoint).
- `Mcp-Session-Id` negotiated on `initialize`; stored in Redis (`mcp:session:{id}` → JSON).
- `POST` body = single JSON-RPC request or batch. Response = JSON-RPC response (sync) **or** upgraded to SSE if `Accept: text/event-stream` and the call streams (`ask_ollie`, `run_experiment`).
- `GET` with `Last-Event-ID` = resumption from the Redis event log.
- `DELETE /api/v1/mcp` with `Mcp-Session-Id` = explicit teardown.
- **`MCP-Protocol-Version` header is REQUIRED on every post-`initialize` request** (transport spec MUST). `ollie-mcp` validates the header on every call and returns `400 Bad Request` with body `{"error": "unsupported_protocol_version", "supported": ["2025-11-25", "2025-06-18"]}` for unknown values. The negotiated version is the one the client echoes back, not necessarily the latest supported.
- **`Origin` header validation is REQUIRED** (transport spec MUST, defense against DNS rebinding). When `Origin` is present, `ollie-mcp` rejects with `403 Forbidden` unless the value matches one of: the canonical client `redirect_uri` host registered via DCR/CIMD, `https://claude.ai`, `https://console.anthropic.com`, or the `Mcp-Session-Id`'s recorded `client_id` origin. Origin absent (typical for non-browser MCP clients) is allowed — Bearer token is the primary defense.
- **Stateless from day one.** Sessions live in Redis, not in process memory; load-balancer stickiness is not required. Rolling deploys never invalidate sessions.
- Backwards compatibility: maintain wire compat for **two minor MCP spec versions back**. URL path versioned (`/api/v1/mcp` → `/api/v2/mcp` on breaking changes).

## 2.5 `ask_ollie` lifecycle — cold start + Tasks + SSE proxy

`ask_ollie` and `run_experiment` are the long-running tools. The cold-start UX problem is hard: on the user's first call, the per-user `ollie-assist` pod may not exist (Helm install + image pull + warmup = up to 2 min). MCP hosts time out on `tools/call` at ~30 s. The solution is the **MCP Tasks primitive** (2025-11-25 spec, currently `experimental` in the official SDK), with a blocking-SSE fallback for hosts that don't yet advertise Tasks support. Capability negotiation happens at `initialize`: `ollie-mcp` advertises `capabilities.experimental.tasks` (the namespace the `mcp` Python SDK emits today from `server.experimental.enable_tasks()`), and the host echoes the same in its `initialize` response if it supports the primitive. When Tasks graduates out of `experimental` in a future spec revision, we flip the advertisement to bare `capabilities.tasks` in lockstep. For each `tools/call` the host opts in to the Tasks shape by including `_meta.task.ttl` in the request `params._meta`; absence of that meta means "use the blocking-SSE path." Tools advertise their willingness to be Tasked via `ToolExecution(taskSupport=TASK_REQUIRED)` on `tools/list` for `ask_ollie` + `run_experiment`; all other tools omit the field (default = not Tasked).

**Tasks engine substrate (Python SDK).** The Tasks lifecycle is implemented on top of the SDK's experimental API — we do NOT hand-roll the JSON-RPC dispatcher. Reference skeleton (lives in `transport/tasks.py`):

```python
from mcp.server import Server
from mcp.server.experimental.task_context import ServerTaskContext
from mcp.types import CallToolResult, CreateTaskResult, TextContent, Tool, ToolExecution, TASK_REQUIRED

server = Server("ollie-mcp")
server.experimental.enable_tasks()  # capabilities.experimental.tasks advertised at initialize

async def handle_ask_ollie(arguments: dict) -> CreateTaskResult:
    ctx = server.request_context
    ctx.experimental.validate_task_mode(TASK_REQUIRED)

    async def work(task: ServerTaskContext) -> CallToolResult:
        # 1. resolve compute, 2. wait for pod ready, 3. proxy SSE, 4. translate to task updates
        await task.update_status("Starting Ollie...")
        # ... full lifecycle ...
        return CallToolResult(content=[TextContent(type="text", text=final_message)],
                              metadata={"thread_id": thread_id, "navigate": navigate, "sources": sources})

    return await ctx.experimental.run_task(work)
```

`transport/tasks.py` adds *our* policy layer on top: TTL enforcement (server cap = host requested `ttl` capped at 10 min), audit log row generation, mTLS callback to comet-backend, and `_meta.task.ttl` propagation from the request.

### Sequence (Tasks-capable host)

```
1. Host  → tools/call {
              name: "ask_ollie",
              arguments: {...},
              _meta: { task: { ttl: 600000 } }
           }
2. ollie-mcp →  comet-backend /api/opik/ollie/compute (with service JWT)
                returns { computeURL, enabled }
3. ollie-mcp →  CreateTaskResult {
                  task: {
                    taskId: "tk_...",
                    status: "working",
                    statusMessage: "Starting Ollie...",
                    createdAt: "<ISO-8601>"
                  },
                  _meta: { "io.modelcontextprotocol/model-immediate-response":
                           "Ollie is starting — this can take up to 2 minutes on first use." }
                }
   (returned in <2 s, before pod is awake)
4. Host LLM tells user "Ollie is starting..."
5. ollie-mcp (async): polls pod's /health/ready (1 s interval, 2-min cap)
6. ollie-mcp → notifications/tasks/updated {
                  task: { taskId, status:"working",
                          statusMessage:"Pod ready, calling agent..." } }
7. ollie-mcp → pod /sessions (service-account JWT + X-Opik-User-API-Key + Comet-Workspace)
8. ollie-mcp ← pod SSE events (thinking, tool_use, ...)
9. ollie-mcp → notifications/tasks/updated for each meaningful event
10. On pod's message_end, ollie-mcp marks the task `status: "completed"` and stores the
    CallToolResult under the task record. The host fetches it via `tasks/get` (or it
    is delivered inline with the final `notifications/tasks/updated` per the host's
    chosen mode). There is no `tasks/result` method in the spec; the result lives on
    the Task object.
```

### Sequence (host doesn't advertise Tasks support)

`ollie-mcp` holds the `tools/call` HTTP response open as SSE, emits `notifications/progress` frames every 10 s during warmup ("Starting Ollie", "Pod ready", "Reading traces", ...), and resolves with `CallToolResult` when done. Identical producer code path; only the response shape differs.

**User-visible UX caveat for the fallback path.** Some hosts (notably current Claude Code CLI and VS Code Copilot) do not render intermediate `notifications/progress` frames as visible UI — only the final `CallToolResult` text. To avoid a silent 2-minute spinner, `ollie-mcp` emits an **early preliminary `CallToolResult` placeholder** within 2 s on `ask_ollie` / `run_experiment` warmup, then sends the real result on completion via a follow-up message:

- For hosts that honor streaming SSE within a single `tools/call`: the first SSE chunk is `{ content: [{type:"text", text:"Ollie is starting — this can take up to 2 minutes on first use. Hold on..."}], metadata: { phase: "warming" } }` and subsequent chunks update or replace.
- For strict hosts that buffer only the final frame: the first response within 2 s is the placeholder text described above (with `isError: false`); the final answer arrives on the host's automatic follow-up call within the SSE session.

This is documented in the host integration guide; the **manual host matrix test** §2.16 must explicitly cover each host's blocking-fallback UX.

### Elicitation fallback policy (for any tool, not just `ask_ollie`)

When a write tool (`comment`, `score`, `save_eval_item`, `add_test_suite_items`, `save_prompt_version`) would prompt the user for confirmation but the host did not advertise `capabilities.elicitation` at `initialize`:

- **Direct write tools** (called by the host LLM directly) proceed without confirmation. The response `metadata.confirmed_without_elicitation = true` is set so the LLM can communicate this to the user in the next turn. Rationale: the LLM picked the tool deliberately; the elicitation was UX polish, not a safety gate.
- **Writes triggered by `ask_ollie`** (the pod emits `confirm_required`) are auto-denied — the pod gets `confirmed: false` back and surfaces "I would have done X but the host doesn't support confirmations" in the streamed text. Rationale: writes initiated by Ollie sub-agents should always be gated; not gating them is a safety regression.

### SSE event translation

For each event from the pod's SSE stream, `ollie-mcp` maps:

| Pod event | MCP frame |
|---|---|
| `thinking_delta`, `message_delta` | `notifications/progress` (text) or `notifications/tasks/updated` |
| `tool_call_start`, `tool_call_end` | `notifications/progress` with structured `stage` marker |
| `compaction_start`, `compaction_end` | `notifications/progress` (best-effort visibility) |
| `confirm_required` | `elicitation/create` → host UI → user response → `POST /sessions/{thread_id}/confirm`. Gated on host advertising `capabilities.elicitation` at `initialize`; when absent, the pod's `confirm_required` is auto-denied and `ask_ollie` returns a `confirmation_required_but_unsupported` notice instead of stalling. |
| `navigate` | buffered; URL validated against allowlist (§2.10.2); emitted in final result metadata and appended to text as Markdown links |
| `error` | terminate SSE, return MCP error per §2.13 |
| `message_end` | mark task `status:"completed"` with `CallToolResult` payload; host retrieves via `tasks/get` |

Final tool result: `{ content: [{ type: "text", text: <final message> }], metadata: { thread_id, navigate?, sources? } }`. The `thread_id` is the contract; hosts pass it back on follow-ups.

### Resumability

`notifications/progress` and SSE events both carry monotonic `seq` IDs. On disconnect, the host reconnects via `GET /api/v1/mcp` with `Last-Event-ID: <seq>`; `ollie-mcp` replays missed events from the Redis event log. The event log key is `(session_id, event_seq) → event` with TTL = pod idle TTL (30 min). Failover across replicas is transparent (Redis-backed).

### Thread lifecycle

- Ollie sessions auto-expire after **30 min** of inactivity (existing behavior); the thread map mirrors that TTL so a stale `thread_id` is recognized as expired without a downstream call. Active use pushes both TTLs forward.
- The thread map is keyed by `(workspace_id, thread_id)`; cross-workspace `thread_id` reuse is impossible by construction.
- On pod restart (helm uninstall on idle), open threads become stale. The next call with the dead `thread_id` returns `thread_expired`; the host retries without `thread_id`. Hosts are instructed to do exactly this via the §2.13 error catalog.

### Long-answer handling

Cap final message at **8,000 output tokens**. When the answer would exceed the cap, `ollie-mcp` stores the overflow under the session record keyed by an opaque `continuation_token` (TTL = 1 hour) and returns the first 8 k tokens with `metadata.continuation_token` set. The host LLM (or user) calls `ask_ollie({continuation_token})` to fetch the next chunk; no new Anthropic call is made for continuations, so they don't count against `ask_ollie` quota.

### Disconnect / resume

If the host disconnects mid-stream, Ollie's session stays alive (30-min idle TTL). The host reconnects — for blocking calls, it issues a `GET` against the original Streamable HTTP session with `Last-Event-ID` set; for Tasks-path calls, it calls `tasks/get` to retrieve current status and any terminal result, plus `GET /api/v1/mcp?Last-Event-ID=…` to replay any missed `notifications/tasks/updated` frames from the Redis event log. Either way, the host can pass the `thread_id` from the prior partial response to issue a new follow-up against the same Ollie session without re-streaming the in-progress answer.

### Phase 1 keep-alive (until MCP Tasks lands in hosts)

The MCP Tasks primitive (`experimental.tasks`) is the eventual long-running solution, but no production host advertises it yet (June 2026 RC at earliest per SEP-2663). In Phase 1, `ask_ollie` is a single `tools/call` whose response stream stays open until `message_end` — which means we must keep host-side tool-call timeouts alive entirely with `notifications/progress`. Two mechanisms, both in `ask_ollie.py`:

1. **One progress notification per pod SSE event.** Every `thinking_delta`, `tool_call_start`, `confirm_required`, etc. is preceded by `ctx.report_progress(progress=<monotonic counter>, message=<event name>)`. Per [MCP spec §Lifecycle/Timeouts](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle), hosts MAY reset their timeout clock on progress notifications — and only progress notifications, not info-level log messages.
2. **A watchdog heartbeat coroutine.** Runs in an `anyio` task group alongside the SSE loop. Polls at half the configured interval (`OPIK_MCP_HEARTBEAT_INTERVAL_S`, default 15 s); when more than that interval has elapsed since the last real-event tick, emits `progress=<counter>, message="streaming"`. The same `events_seen` counter is shared between the heartbeat and the SSE loop so progress remains strictly monotonic per spec.

Hosts behave differently here. **Claude Code** and **MCP Inspector** (with `MAX_TOTAL_TIMEOUT` raised) honor the reset and stay open indefinitely. **Cursor** has a 60-second hard tool-call cap that doesn't reset on progress notifications — operations longer than ~60 s will fail on Cursor regardless, documented as a known limitation in the README. When Tasks support graduates and lands in hosts, we re-enable the substrate sketched above in this section; the heartbeat goes away in favor of `notifications/tasks/updated`.

## 2.5.1 User identity propagation — `ollie-mcp` → pod → opik-backend

When MCP user X (in workspace Y) calls `ask_ollie`, Ollie must read/write Opik data **as X**, not as Ollie's deployment service account. Two auth-source paths, one downstream shape.

**Path A — MCP user authenticated via API key.**
`ollie-mcp` already holds the user's Opik API key (in the `Authorization` header). It passes that key directly to the pod in `X-Opik-User-API-Key`. The pod's `get_or_create_user_opik_client(workspace)` uses it for downstream Opik calls. No new credentials minted.

**Path B — MCP user authenticated via OAuth (the common cloud path).**
`ollie-mcp` has a user JWT, not an Opik API key. On `tools/call ask_ollie`, it first checks `mint_cache` (Redis) for an existing minted key keyed by `(jwt_token_id, workspace_id)`. **Cache hit** (the common case — keys live 1 h, most users issue many calls within an hour): reuse it. **Cache miss:** mTLS callout to comet-backend `/oauth/mint-user-api-key` with a service-account JWT and `{user_id, workspace_id}`; comet-backend issues a short-lived (1 h) Opik API key; `ollie-mcp` stores it in `mint_cache` with the key's TTL minus 5 min safety margin and passes it to the pod in `X-Opik-User-API-Key`. Audit log records `(jwt_token_id, minted_api_key_id)`; on JWT revocation comet-backend publishes a revoke event that `ollie-mcp` consumes to evict the cache entry within 60 s.

**Why mint instead of using a fixed shared key.** A fixed key would either (a) tie all MCP-via-OAuth traffic to one Opik user account (wrong workspace permissions; wrong audit attribution) or (b) require Ollie to maintain N user-keys in env (operationally awful). Minting a per-call key inherits the user's real Opik permissions, attributes audit correctly, and auto-expires.

**Why not let `ollie-mcp` hit Opik directly with the user JWT, skipping API-key issuance.** Opik backend's `RemoteAuthService` only accepts cookie or API-key paths. Adding a JWT-validation path to Opik would be more invasive than minting a short-lived API key.

```
MCP host  ─── tools/call ask_ollie (user JWT) ───►  ollie-mcp
                                                        │
                                                    [OAuth path]
                                                        │ mTLS + service JWT + (user_id, workspace_id)
                                                        ▼
                                                  comet-backend  ─── /oauth/mint-user-api-key
                                                        │
                                                        │ short-lived Opik API key
                                                        ▼
                                                    ollie-mcp  ── service JWT + X-Opik-User-API-Key ──►  pod
                                                                                                          │
                                                                                                          │ Authorization: <key>
                                                                                                          ▼
                                                                                                     opik-backend
```

## 2.5.2 Pod-side JWT verifier

The pod's nginx gate validates the service-account JWT before requests reach the Python app.

**Image + library pins:**
- Base image: **OpenResty 1.25.3.2** (`openresty/openresty:1.25.3.2-alpine`) — bundled `lua-nginx-module` + LuaJIT + `lua-resty-http`.
- OAuth/JWT verification: **`lua-resty-openidc` v1.7.6** (pinned; the `opts.discovery` semantics differ across minor versions). Installed via `opm get zmartzone/lua-resty-openidc=1.7.6` in the image build.
- Transitive: `lua-resty-session` v4.0.5, `lua-resty-jwt` v0.2.3 (locked in the same `opm` step).

**X-Internal-* strip is at the server block, not per-location.** Per-location stripping leaves a hole if a new `location` block is added later without the directive. Strip applies to the entire pod's nginx server so no caller anywhere on the pod can inject these headers:

```nginx
server {
    listen 8080;
    server_name _;

    # Server-wide: NO caller (browser or service-account, any path) may inject internal headers.
    # New locations inherit this automatically.
    more_clear_input_headers "X-Internal-*";

    # Service-account path (ollie-mcp callers)
    location /sessions {
        access_by_lua_block {
            local opts = {
                discovery = "https://accounts.comet.com/.well-known/jwks.json",  -- specific host, not wildcard
                ssl_verify = "yes",
                ssl_verify_pinned_sha256 = os.getenv("JWKS_HOST_SHA256_PIN"),    -- exact accounts.comet.com pubkey pin
                jwks_cache_size = 1000,
                jwks_cache_ttl = 3600,        -- 1h; refresh on kid miss + on signature-verify failure
                allowed_iss = "comet-mcp",
                allowed_aud = "pod",
                jwt_signing_alg_values_expected = { "RS256" }
            }
            local res, err = require("resty.openidc").bearer_jwt_verify(opts)
            if err then ngx.exit(401) end
            -- Bind to this pod's tenant. The pod is provisioned per-workspace
            -- by codepanels; refuse JWTs whose workspace claim differs from
            -- the pod's environment-supplied tenant.
            if res.workspace ~= os.getenv("POD_WORKSPACE_ID") then ngx.exit(401) end
            ngx.req.set_header("X-Internal-User-Id", res.sub)
            ngx.req.set_header("X-Internal-Workspace-Id", res.workspace)
            ngx.req.set_header("X-Internal-Jwt-Id", res.jti)
        }
        proxy_pass http://app;
    }

    # Other locations (/sessions/{id}/messages, /sessions/{id}/confirm, /health, /sessions/browser)
    # inherit the server-wide X-Internal-* strip. Each defines its own auth check
    # (service JWT for /sessions/*, browser cookie for /sessions/browser).
}
```

**JWKS cert pinning** uses the specific JWKS host (`accounts.comet.com`) and an SHA-256 public-key pin (`JWKS_HOST_SHA256_PIN` env, sourced from a k8s Secret rotated quarterly). A wildcard `*.comet.com` pin would let a dangling subdomain takeover bypass verification — explicitly avoided.

JWKS cache TTL is **1 h** so a revoked key reaches all pod verifiers quickly; cache invalidation on `kid` miss and on signature-verify error forces an immediate refresh. Comet-backend exposes `POST /internal/jwks/invalidate` (mTLS-only) that pushes a Redis pub/sub message read by every verifier (`ollie-mcp` and pod-side) to evict the in-memory cache on emergency rotation. The `ollie-mcp`-side cache lives in `auth/oauth.py` with the same 1 h TTL + on-kid-miss refresh + pub/sub invalidate listener.

**Mcp-Session-Id binding hardening (against the rotation chain).** Sessions are bound to the **current** access-token `jti`, not any rotation ancestor. When `/oauth/token` refresh issues a new access-token JWT, the prior `Mcp-Session-Id` is **rebound** to the new `jti` (atomic Redis transaction); rotation depth is **bounded at 1 hop** for binding purposes. Presenting a token whose `jti` matches neither the current binding nor the most recent ancestor → 401 `session_token_mismatch` and the session is closed. This bounds replay-window damage from a stolen refresh-token-rotated access token to the lifetime of a single rotation cycle.

**Continuation-token binding.** The `continuation_token` (§2.5 long-answer handling) is stored with the originating session's `(sub, workspace_id)` and only retrievable by a `tools/call` from a session whose JWT matches both fields — a leaked continuation token cannot be redeemed cross-user.

**`X-Opik-User-API-Key` log scrubbing** is applied at every tier that logs HTTP requests:
- ollie-mcp structlog: redaction filter rejects any value matching `^opik_(api|sk)_[a-zA-Z0-9]+`.
- ollie-assist nginx access log: `log_format` omits the request header set; OpenTelemetry httpx/FastAPI auto-instrumentation has `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*` set to a deny-list including this header.
- opik-backend access logs already scrub `Authorization`; extend the scrubber to `X-Opik-User-API-Key`.

## 2.6 Read surface implementation

Per ADR 0004 D1, reads do not use the MCP `resources` primitive. The implementation lives under `src/opik_mcp/read_list/`:

- **`registry.py`** — `ENTITY_REGISTRY` keyed by entity type. Each `EntityHandler` carries: `fetch_fn` (singleton GET), optional `search_by_name_fn` (substring index used by `read` when the input isn't a UUID), optional `list_fn` + `list_required_kwargs` (workspace- or parent-scoped enumeration), and an optional `compress_fn` override. Composite reads (`trace` = trace + spans, `prompt` = prompt + versions) hide the fan-out inside `fetch_fn`.
- **`read_tool.py`** — `run_read(entity_type, id, *, max_tokens)`. Dispatch order: URI parse (`opik://...`) → registry lookup → UUID-vs-name branch → fetch → compress. Typed `OpikClient` errors map to `ToolError` with status-specific guidance.
- **`list_tool.py`** — `run_list(entity_type, *, name, page, size, project_id, test_suite_id, prompt_id)`. Renders a pipe-delimited table with `(id, name, …entity-specific extras)` plus a `Use page=N for next M results.` footer when more pages exist. Required parent kwargs are validated against `list_required_kwargs` before any REST call.
- **`compression.py`** — `CompressionTier` enum + token-budget logic. Default budget 8 000 tokens (override via `max_tokens`); the trace skeleton tier kicks in at 50 000 tokens. The estimator is `len(json) // 4` (crude but consistent across tiers).
- **`uri.py`** — `opik://` → `(entity_type, id)` parser; raises `InvalidURI` for malformed inputs so the caller can distinguish "user passed a UUID" from "user passed a bad URI".

**Pagination.** Both tools use `(page, size)` (1-indexed page, size capped at 100). Spring Page envelope from opik-backend (`content`, `page`, `size`, `total`) flows through untouched.

**No read cache.** Reads go straight to the API on every call. The MCP host (Claude Code, Cursor, …) already has prior tool results in its conversation context, so duplicate `read` calls in a session are rare; a per-session LRU would add an in-process LRU + lock + session-id plumbing for ~zero hit rate in normal use. If write-after-read invalidation becomes a real concern later, the cache layer is easy to reintroduce — but starting without it keeps the surface obvious.

## 2.7 Opik 2.0 audit deliverable

Output: `docs/superpowers/audits/2026-05-12-opik-mcp-toolset-audit.md`, one row per existing `opik-mcp` tool:

| Tool | Toolset | Opik 2.0 status | Action |
|---|---|---|---|
| `list_prompts` | core | works | keep |
| `get_trace_thread_metrics` | metrics | endpoint moved | fix or drop |
| … | | | |

For each entry mark: works / broken / superseded by `ask_ollie` / better as a resource. Cross-link to the migration matrix.

**Migration matrix** (old `opik-mcp` tool → hosted MCP equivalent):

| Old `opik-mcp` tool | Hosted equivalent |
|---|---|
| `list_projects`, `get_project` | `list("project", …)`, `read("project", <id>)` |
| `list_traces`, `get_trace` | `list("trace", project_id=…)`, `read("trace", <id>)` |
| `get_trace_thread_metrics` | `ask_ollie("metrics for trace thread X")` or direct Opik REST |
| `list_prompts`, `get_prompt` | `list("prompt", …)`, `read("prompt", <id>)` |
| `list_datasets`, `get_dataset_items` | `list("test_suite", …)`, `list("test_suite_item", test_suite_id=…)` — legacy "datasets" are test suites in Opik 2.0 |
| `list_experiments`, `compare_experiments` | `list("experiment", …)`, `read("experiment", <id>)` plus `ask_ollie` for comparison |
| `create_dataset_item` | Tool: `add_test_suite_items` (batch shape; legacy single-item callers send a 1-element array) |
| `log_trace` | Tool: `create_trace` (renamed to match Python SDK `client.trace()`) |
| (none — was Opik REST only) | Tool: `score`, `comment`, `save_prompt_version`, `run_experiment`, `save_eval_item` |
| `expert-*-actions` toolsets | `ask_ollie` |

CI scripts that depend on `opik-mcp` keep working. Migration is opt-in.

## 2.8 Self-hosted packaging

`docker-compose.yml` (Opik bundle):
```yaml
ollie-mcp:
  image: ghcr.io/comet-ml/ollie-mcp:${OPIK_VERSION}
  environment:
    - OPIK_BACKEND_URL=http://opik-backend:8080
    - POD_URL=                                      # optional; static pod URL for self-hosted (no codepanels)
    - JWKS_URL=                                     # optional; for operator-configured OAuth
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}      # only needed by Ollie pod
    - AUTH_MODE=apikey                              # apikey | oauth | both
    - OPIK_MCP_ALLOW_UNAUTH=false                   # set true only if you really run Opik without auth
    - DAILY_QUOTA_ASK_OLLIE=                        # operator chooses
    - DAILY_QUOTA_RUN_EXPERIMENT=
    - REDIS_URL=redis://redis:6379/0
  ports: ["7777:7777"]

ollie-assist:
  image: ghcr.io/comet-ml/ollie-assist:${OPIK_VERSION}
  profiles: ["ai"]                                  # docker compose --profile ai up
  environment:
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}        # required
    # ... existing ollie-assist env
```

Helm chart adds equivalent deployment + service + ingress. Startup checks:
- If `OPIK_NO_AUTH=true` upstream and `OPIK_MCP_ALLOW_UNAUTH=false`, `ollie-mcp` logs an error and exits.
- If `ANTHROPIC_API_KEY` is empty, `ollie-assist` does not start; `ollie-mcp` omits `ask_ollie` / `run_experiment` from `tools/list`.

## 2.9 Comet Cloud deployment

| Item | Decision |
|---|---|
| **Cluster** | Same K8s cluster as opik-backend; new Deployment + Service for `ollie-mcp`. |
| **Replicas** | ≥2 minimum (always-warm); HPA on CPU + request rate. PodDisruptionBudget `minAvailable: 1` so a node drain never takes both replicas down at once. |
| **Ingress** | Path-based on existing `www.comet.com` ingress → Service. |
| **Service discovery** | K8s DNS for internal calls. mTLS service mesh (Istio / Linkerd, whichever Comet runs) for `ollie-mcp` ↔ comet-backend `/oauth/{mint-user-api-key, internal/mcp/audit}`. |
| **Secret management** | Vault → projected secrets. RS256 signing key (user + service pairs), mTLS client cert, Redis creds. |
| **Redis topology** | **Single primary Redis** in the home region per workspace, with read replicas if traffic warrants. `ollie-mcp` is single-region per request — a workspace's sessions / event log / quota counters all live in one region. Multi-region is therefore *region pinning*, not a global Redis cluster: an EU-residency workspace routes to the EU `ollie-mcp` Deployment + EU Redis instance; a US workspace to US. `REDIS_URL` is per-region in the Vault config. Cross-region session migration is not supported by design — it'd require encoding the session-region in the JWT and re-pinning on every request. |
| **Multi-region launch posture** | One region at launch (US-east) for both `ollie-mcp` and comet-backend OAuth AS. EU `ollie-mcp` Deployment + EU Anthropic routing for `ask_ollie` ships in W10 (gates EU residency claims; until then, the consent screen warns EU workspaces that data flows to US Anthropic). |
| **Rolling deploy / SSE drain** | `terminationGracePeriodSeconds: 180`. preStop hook (1) flips the readiness probe to "not ready" via an internal `/shutdown/start` endpoint (mTLS-gated, so ingress stops sending new traffic), (2) sleeps 15 s to let load-balancer drain caches expire, then (3) walks the live session map and pushes `notifications/tasks/updated` with `statusMessage: "Server is recycling; the session will resume on reconnect"` on each in-flight SSE stream before closing it. Tasks-in-flight metadata is handed off to Redis (`mcp:task:{task_id}` set during `ctx.experimental.run_task`); a different replica picks up `tasks/get` polling against the same key. Hosts reconnect via `Last-Event-ID` against a healthy replica (sessions live in Redis). Drain-success SLO in §2.11.1. |
| **Horizontal scaling** | HPA on **`ollie_mcp_sse_active_connections`** custom metric (target 200 / pod) **AND** CPU (target 60%) — whichever fires first. SSE-connection count is the leading signal because `ask_ollie` is long-running and CPU lags. Custom metric exported by the Prometheus adapter. Minimum 2 / region, max 20 / region. |
| **Thundering-herd on cold pod** | Multiple `ask_ollie` calls for the same `(workspace_id)` while the pod is still warming would each call `/api/opik/ollie/compute` and poll `/health/ready` separately. Mitigation: a **Redlock**-style distributed lock `mcp:warmup:{workspace_id}` (Redis `SET NX PX 30000` with the leader's pod identity `{pod_name}:{uuid}` as the value, re-acquired every 15 s until the pod is warm) is taken by the first caller; subsequent callers in the warmup window subscribe to `mcp:warmup:{workspace_id}:events` and read the resolved compute URL once the leader writes it. The 30 s TTL is shorter than the 2-min warmup ceiling so a crashed leader releases the lock within bounded latency; a watchdog goroutine on the leader extends the lease while warmup is in progress. On lock expiry without a result, callers retry from scratch; we **do not** auto-elect a new leader to avoid duplicate pod boots. |
| **Redis failover behavior** | `redis-py` async client with health-check + reconnect + 5 s circuit-breaker. **Reads fail-open**: if Redis is down, `read` / `list` tool calls go directly upstream without quota lookup (caller hit-counted post-flight when Redis recovers). **Writes fail-closed**: `tools/call` for write tools rejects with `503 redis_unavailable` because we cannot enforce quota or update audit; `ask_ollie` rejects with the same code because session state cannot be persisted. SLO breach is logged but not paged on transient (<30 s) outages; >60 s pages SRE. |
| **DR & backup** | OAuth control-plane tables (`mcp_oauth_clients`, `mcp_refresh_tokens`, `mcp_authorization_codes`, `mcp_audit_log`, `mcp_mint_audit`, `mcp_jwks_cache`) inherit comet-backend's MySQL backup posture (point-in-time recovery, 30-day retention, daily snapshot to S3 cross-region). RPO 5 min / RTO 1 h. `mcp_audit_log` additionally writes to WORM S3 (Object Lock, 7-year retention) via the daily archive worker; in DR-restore, audit chain hash continuity is verified against the WORM copy before resuming writes. Redis is intentionally **not** backed up — session/quota state is recoverable from log replay; OAuth refresh-tokens are in MySQL, not Redis. |
| **Blue/green** | Stateless across replicas (Redis-backed sessions). Rolling deploys work without coordination. |
| **Owner** | Opik core operates `ollie-mcp`; Comet platform operates the OAuth endpoints in `comet-backend`. Shared on-call rota for `/api/v1/mcp/*`. |

## 2.10 Security

### 2.10.1 Already-covered fundamentals

JWT signing (RS256), PKCE, refresh rotation, RFC 8707 resource indicators (tokens bound to `https://www.comet.com/api/v1/mcp` audience), rate limiting, audit log, workspace isolation.

### 2.10.2 Specific hardening

| Concern | Decision |
|---|---|
| **JWT algorithm** | RS256 from day one. Public keys at `/.well-known/jwks.json`. Two keys live (current + previous) during rotation. Monthly automatic rotation. **Separate key pairs for user tokens vs service-account tokens** (different `kid` namespaces: `user-<n>` and `svc-<n>`). Compromise of one does not require rotating the other; misuse of a service key against a user-audience verifier is rejected on `aud` mismatch. Both keys are published in the same `jwks.json`. |
| **Refresh-token reuse** | Detect on use-after-rotation. Revoke entire client family on violation; log a security alert. |
| **`Mcp-Session-Id`** | Generated by `ollie-mcp` on `initialize` as a 32-byte cryptographically random base64url string (192 bits entropy). Bound at issuance to the resolving JWT's `(sub, workspace_id, jti)`; every subsequent request with that session ID must present a JWT with the same `(sub, workspace_id)` and a `jti` that is either the original or a refresh-rotated descendant (tracked in `mcp_refresh_tokens.rotated_from`). Mismatch → 401 + session invalidation. Resumption (`GET /api/v1/mcp` + `Last-Event-ID`) is gated on the same binding. |
| **DCR abuse** | Per-`software_id` (200/day) + per-IP (50/hour) + anonymous platform-wide (200/day total). Require CIMD or interactive consent. Fields HTML-escaped; raw HTML/`javascript:`/`data:` URLs rejected. |
| **`redirect_uri` normalization** | OAuth code/token endpoints compare submitted `redirect_uri` against the registered set via byte-exact match **after** normalization (scheme + host lowercased; default-port stripped; path percent-decoded once; fragment rejected). Loopback hosts (`http://127.0.0.1` / `http://[::1]` / `http://localhost`) match port-agnostically per RFC 8252; all other schemes are byte-exact. No prefix or wildcard matching. |
| **CSRF on `/oauth/authorize` POST** | Existing Comet CSRF token infrastructure (double-submit cookie pattern). Token verified before issuing code. **CSRF tokens are bound to `(session_id, workspace_id)`**: the GET handler embeds the `workspace_id` the user is consenting for into the token payload; the POST handler verifies the submitted `workspace_id` form value matches. This prevents a CSRF on workspace A from issuing a code against workspace B (e.g., when the user is logged into multiple workspaces). |
| **CORS** | `/api/v1/mcp` rejects browser cross-origin requests (no `Access-Control-Allow-Origin`); MCP clients are not browsers. `/oauth/authorize` is same-origin. |
| **Token leakage in logs** | Audit log stores `token_id` (sha256 prefix), never the JWT. Centralized log scrubber rule. |
| **Consent screen XSS / clickjacking** | All template-rendered fields from user-controlled or DCR-supplied sources (workspace name, client name, redirect URIs, scope display strings) are HTML-escaped at render via the existing Comet template auto-escape. Server-side reject of DCR fields containing `<script>`/`javascript:`/`data:` at registration time. Response headers on `/oauth/authorize` GET: `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; form-action 'self'`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. |
| **`notifications/cancelled` auth** | The cancellation notification is delivered in-band on the main `/api/v1/mcp` endpoint and inherits its Bearer-JWT auth; the JWT's `(sub, workspace_id)` must match the session and Mcp-Session-Id the cancelled task was issued under. Internal operator-side cancellation (debugging only) goes through the `/internal/*` mTLS-gated routes in §2.2.1. |
| **`navigate` URL allowlist** | `comet.com`, `*.comet.com`, plus explicit per-workspace operator allowlist. Subdomain-takeover guard: an SRE-owned daily check verifies all `*.comet.com` subdomains have current TLS certs and resolve to Comet-controlled infrastructure; any dangling subdomain is removed within 24 h. |
| **Prompt injection via trace data** | Ollie already hardens against this via its existing system prompt + tool-confirm gates. Document the residual risk; do not surface untrusted trace content as direct system prompt to host. |
| **Self-hosted noauth refusal** | `ollie-mcp` checks Opik `/health` includes `auth_enabled: true` at startup; exits if false and `OPIK_MCP_ALLOW_UNAUTH != true`. **Periodic re-check**: a background task re-polls `/health` every 60 s; if `auth_enabled` flips from `true` to `false` mid-run (operator misconfiguration), `ollie-mcp` logs a security event, refuses all new `/api/v1/mcp` requests with 503 `auth_disabled_runtime`, and exits within 5 minutes (giving in-flight Tasks a chance to finalize). The window prevents a long-running server from drifting into an unauthenticated posture. |
| **Self-hosted Anthropic quota** | When `AUTH_MODE=apikey` and the operator has not explicitly set `DAILY_QUOTA_ASK_OLLIE`, the value **defaults to 0** (i.e., `ask_ollie` is disabled and omitted from `tools/list`). Operators must opt in by setting a positive quota. Rationale: a self-hosted operator who installs the bundle without knowing about Anthropic costs should not silently start spending; `0` makes the cost choice explicit. The CLI startup banner prints `ask_ollie disabled (DAILY_QUOTA_ASK_OLLIE unset); set to a positive value to enable`. |
| **mTLS for `/oauth/mint-user-api-key`** | Client cert pinned to `ollie-mcp` ServiceAccount. K8s NetworkPolicy restricts ingress to that ServiceAccount only. **CA rotation**: certs issued by an internal CA (cert-manager + `ClusterIssuer`); rotation cron rotates leaf certs every 30 days with 7-day overlap. CA itself rotates yearly with 90-day overlap — both old and new CA pinned in comet-backend's truststore during the overlap window. Emergency CA rotation runbook in `docs/runbooks/ollie-mcp.md`: revoke client cert (CRL push), reissue under new CA, re-deploy `ollie-mcp` with refreshed projected secret, drop old CA from truststore. |
| **mint-user-api-key blast radius** | Per-`(user_id, workspace_id)` cap of 60 mints/hour independent of mTLS; every mint (success or failure) writes to `mcp_mint_audit`; anomaly alerts on aggregate and per-workspace mint rate. |
| **Service-account JWT** | RS256, 5-min `exp`, `aud` claim per-target (`comet-backend` vs `pod`). Signed with the **service-key pair** (separate from user-token signer per the row above); rotation cron rotates user and service keys on staggered schedules so an emergency rotation of one does not require coordinating the other. |
| **JWKS cache poisoning** | Verifiers (ollie-mcp, pod nginx) fetch over HTTPS with cert pinning to `*.comet.com`; cache by `kid`; refresh on miss. Failure to fetch fails the request closed. JWKS cache TTL **1 h** (not 24 h) so revocation propagates quickly; `POST /internal/jwks/invalidate` (mTLS-only) triggers an immediate eviction via Redis pub/sub for emergency rotation. |

### 2.10.3 Scopes

Granular scopes from day one (§1.8 table). Tokens carry the union the user consented to:
- `mcp:read` — required for `read` and `list` tool calls. Future docs lookup (`ollie-assist` docs index) will land under this same scope when published; docs are workspace-agnostic but still require `mcp:read` so anonymous tokens (none exist today, but reserved) cannot reach them.
- `mcp:write:traces` — `create_trace`, `create_span`.
- `mcp:write:annotations` — `score`, `comment`.
- `mcp:write:test_suites` — `add_test_suite_items`, `save_eval_item`.
- `mcp:write:prompts` — `save_prompt_version`.
- `mcp:write:experiments` — `run_experiment` (write side).
- `mcp:ask_ollie` — `ask_ollie`.
- `mcp:run_experiment` — `run_experiment` (invocation; kept separate from `mcp:write:experiments` so a user can grant the write side without the long-running invocation, and so a future admin surface (ADR 0006) can disable runs without disabling other write families).

Refusing every requested scope at consent = `error=access_denied` to host.

**Per-workspace admin disables — not part of launch (ADR 0006).** Scope enforcement is purely at the OAuth-grant level: the user's granted-scope set on each token determines what tools they can call. There is no workspace-level override table at launch.

**Ollie-initiated writes still require the matching token scope.** When `ask_ollie` plans an action that calls a write tool (e.g., Ollie proposes to add an item to a test suite), `ollie-mcp` checks the user's token scopes **before** dispatching the underlying write — independent of the user's `elicitation/create` response. The elicitation is *user intent*; the scope is *what the user agreed to at consent*. A user who did not grant `mcp:write:test_suites` cannot have Ollie write test-suite items just because they clicked "Approve" on the elicitation. Missing scope returns `scope_missing` / `Token lacks scope {scope}` and Ollie surfaces a "this requires the `mcp:write:test_suites` scope, which this connector didn't request — reinstall the connector with the additional scope" hint in the next turn.

## 2.11 Observability & SLO

### 2.11.1 SLOs

| SLI | Target | Window |
|---|---|---|
| `/oauth/*` availability | 99.9% | 30d |
| `/api/v1/mcp` availability | 99.9% | 30d |
| `tools/call` p50 latency (direct writes) | <500 ms | 7d |
| `tools/call` p99 latency (direct writes) | <2 s | 7d |
| `ask_ollie` p50 latency to **initial response** (Tasks `CreateTaskResult` or blocking-SSE first byte) | <500 ms | 7d |
| `ask_ollie` p50 latency to final result (**warm pod**: `/health/ready` already 200 at call time) | <30 s | 7d |
| `ask_ollie` p99 latency to final result (**warm pod**) | <60 s | 7d |
| `ask_ollie` p99 latency to final result (**cold pod**: pod was scaled to zero at call time) | <150 s, with 2-min warmup ceiling | 7d |
| `ask_ollie` cold-pod fraction (of all `ask_ollie` calls) | <15% | 7d (used to validate warm/cold buckets aren't gamed) |
| `run_experiment` p99 latency to initial response (Tasks creation, or blocking enqueue ack) | <2 s | 7d |
| `read` / `list` tool p99 latency | <1 s | 7d |
| Error rate (5xx) | <0.5% | 7d |
| SSE drain success on rolling deploy (sessions terminated cleanly within `terminationGracePeriodSeconds`) | >99% | 30d |

### 2.11.2 Dashboards
1. **Request volume** — by tool, by tier, by workspace.
2. **Latency** — p50/p95/p99 per endpoint and per tool, with cold-vs-warm pod breakdown for `ask_ollie`.
3. **Error rates** — by status code, by error class.
4. **Auth health** — token issuance, refresh, rejection rates; OAuth funnel.
5. **Quota usage** — daily usage per workspace; over-cap rejections.
6. **`ask_ollie` cost** — Anthropic token spend per workspace per day.
7. **Streaming health** — SSE connection lifetime, reconnects, session map size, event log size.
8. **Mint-key flow** — mints/hour (aggregate, per workspace), `mint_cache` hit ratio, mTLS verification failures.
9. **Pod warmup** — `/health/ready` poll duration distribution; cold-start frequency; pod-up time vs. tools/call arrival.
10. **JWKS health** — `kid` cache hit ratio (both `ollie-mcp` and pods); JWKS fetch failures.

### 2.11.3 Alerts
- `/api/v1/mcp` 5xx > 1% over 5 m.
- `/oauth/token` failure rate > 5% over 5 m.
- `ask_ollie` p99 to final result > 180 s over 15 m.
- Aggregate `ask_ollie` Anthropic cost (all workspaces) > 2× 30-day-trailing daily average **AND** above an absolute floor of $200/day (floor reviewed quarterly); per-workspace alert at 5× workspace-trailing average AND > $20/day floor.
- Quota tracker / Redis unavailable.
- Refresh-token reuse detected (security page).
- mTLS verification failures > 10 over 5 m → security page.
- Mint-rate anomaly: aggregate `mint-user-api-key` rate > 3× 7-day-trailing average, or any single `(user_id, workspace_id)` pair > 50 mints/hour, or any single workspace > max(120, 4× workspace-trailing-7-day-hourly-average) → security page.
- RS256 key rotation overdue (>45 days since last rotation) → SRE alert.
- Subdomain-takeover check failed for an entry on the `navigate` allowlist → security page.
- Pod warmup p99 > 180 s sustained for 15 m → pod-orchestrator on-call page.

### 2.11.4 Prometheus metric names

Standardized so internal SRE dashboards (§2.11.2) and alerts (§2.11.3) reference the same series. All metrics carry `workspace_id`, `region`, and `pod_state` (`warm` | `cold`) labels where applicable; cardinality on `workspace_id` is bounded by §2.12 retention (active workspaces only). Per ADR 0006, customer-facing dashboards are out of scope for launch; the metrics here drive internal Grafana only.

| Name | Type | Labels | Notes |
|---|---|---|---|
| `ollie_mcp_tools_call_duration_seconds` | histogram | `tool`, `pod_state`, `outcome` | Buckets: `0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300`. `outcome` = `success` \| `error` \| `quota_exceeded` \| `scope_disabled`. |
| `ollie_mcp_resources_read_duration_seconds` | histogram | `entity`, `cache` | `cache` = `hit` \| `miss` \| `bypass`. |
| `ollie_mcp_oauth_funnel_total` | counter | `step`, `outcome` | `step` = `authorize_get` \| `authorize_post` \| `token` \| `refresh` \| `revoke` \| `register`. |
| `ollie_mcp_sse_active_connections` | gauge | `region` | HPA scaling signal (§2.9). |
| `ollie_mcp_sse_drain_result` | counter | `result` | `result` = `clean` \| `forced` \| `timeout`. Drives SSE-drain SLO. |
| `ollie_mcp_pod_warmup_state` | gauge | `workspace_id` | `0` = none, `1` = warming, `2` = warm. |
| `ollie_mcp_pod_warmup_duration_seconds` | histogram | `outcome` | `outcome` = `success` \| `timeout` \| `error`. |
| `ollie_mcp_cold_pod_fraction` | gauge | (none) | Rolling 7-day fraction; alert at >15%. |
| `ollie_mcp_anthropic_tokens_total` | counter | `workspace_id`, `model`, `kind` | `kind` = `input` \| `output` \| `cache_read` \| `cache_write`. Drives the internal SRE cost view. |
| `ollie_mcp_quota_remaining` | gauge | `workspace_id`, `tool` | Real-time. |
| `ollie_mcp_jwks_cache_total` | counter | `service` (`ollie_mcp` \| `pod`), `outcome` (`hit` \| `miss` \| `fetch_failure`) | Drives JWKS health dash. |
| `ollie_mcp_mint_total` | counter | `workspace_id`, `outcome` | `outcome` = `success` \| `rate_limited` \| `mtls_failure` \| `db_unavailable`. |
| `ollie_mcp_audit_log_chain_breaks_total` | counter | (none) | Should be 0; any non-zero pages security. |
| `ollie_mcp_session_invalidations_total` | counter | `reason` | `reason` = `jwt_mismatch` \| `refresh_reuse` \| `token_revoked`. |

Metric names are stable wire contract for dashboards and alert rules; any rename requires a deprecation cycle (both names exported for 1 release).

### 2.11.5 Manual host matrix

Before each release, manually exercise the canonical capability matrix. Failures gate the release.

| Host | OAuth | Tasks | Elicitation | Resources | Notes |
|---|---|---|---|---|---|
| claude.ai (web) | ✅ | ✅ | ✅ | ✅ | Reference host. |
| Claude Code (CLI) | ✅ | ✅ | ✅ (terminal prompts) | ✅ | DCR-only path; tests #38102 / #26675 fixes. |
| Cursor | ✅ | ⚠️ blocking-SSE fallback | partial | ✅ | Verify `ask_ollie` blocking-SSE path. |
| VS Code Copilot | ✅ | ⚠️ blocking-SSE fallback | partial | ✅ | Pre-registered clientId path. |
| MCP Inspector | n/a (`?token=`) | ✅ | ✅ | ✅ | First-line dev check. |

✅ = supported and tested. ⚠️ = degraded but functional. partial = host accepts elicitation but UI is rudimentary.

Add a host? Add a row, run the smoke checklist (`make host-matrix HOST=<name>`), and update the row with the outcome. Removing a host from this matrix requires a workstream owner sign-off.

### 2.11.6 Runbook
Lives in `docs/runbooks/ollie-mcp.md`. Covers: scale-out procedure, RS256 key rotation, downstream service degradation (pod / opik-backend / React service / Anthropic), quota emergency override, mTLS cert rotation, JWKS cache poisoning recovery, **RS256 key compromise** (immediate revocation via `POST /internal/jwks/invalidate`, emergency key rotation cron trigger, pub/sub fan-out to every `ollie-mcp` replica + pod nginx, audit-log walk to find all tokens issued by the compromised `kid`, force re-auth on affected sessions via `mcp_session_invalidations_total{reason="refresh_reuse"}` push), **mTLS CA compromise** (CRL push, force `ollie-mcp` redeploy with new client cert, rotate truststore on comet-backend, freeze `/oauth/mint-user-api-key` for the duration of the rotation window), **audit-chain break detected** (`ollie_mcp_audit_log_chain_breaks_total > 0`: stop the daily archive worker, capture the last known good chain hash from the WORM S3 copy, snapshot the affected `mcp_audit_log` partitions, escalate to the security on-call before resuming writes — chain breaks indicate either a bug or tampering and require human confirmation).

## 2.12 Compliance

| Topic | Decision |
|---|---|
| **DPA addendum** | Drafted with legal at launch (§1.14). Covers Anthropic data flow. |
| **Audit log retention** | 12 months default. Per-tenant override up to 84 months (7 years) for regulated customers. |
| **Data residency** | US Anthropic region at launch with EU customers warned; EU routing via Anthropic EU region ships in W10. **Until W10 is GA, the consent screen for EU-tagged workspaces shows a banner** ("Data sent to Ollie is processed in the US. EU residency ships {date}.") and `ask_ollie` carries a `metadata.region: "us-east"` field on every result so EU customers can audit. EU-residency contractual claims are not made until W10 lands. |
| **GDPR data subject requests** | Audit log + Opik trace data both indexed by user_id; deletion procedure documented. |
| **SOC 2 controls** | `ollie-mcp` inherits Opik's existing SOC 2 perimeter; new control = OAuth client lifecycle + mint-key audit. |
| **PII handling** | Trace data sent to Anthropic in `ask_ollie` may contain customer PII. Document in DPA; provide per-workspace opt-out of `ask_ollie`. |

## 2.13 Error response taxonomy

What hosts see for common failure modes. Used in tool result `isError: true` with a `content` array.

| Condition | HTTP/MCP | Message |
|---|---|---|
| Token missing/invalid | 401 (MCP unauthorized) | `Authentication required` — triggers host refresh |
| Token revoked | 401 | `Authentication revoked. Please reconnect.` |
| `ask_ollie` disabled | 403 (`ask_ollie_disabled`) | `ask_ollie is disabled for workspace {name}. Other MCP tools still work.` — next `tools/list` omits `ask_ollie`. |
| Scope insufficient | 403 | `Token lacks scope {scope}.` |
| Tool unavailable (self-hosted no Ollie) | 404 (method-not-found) | Tool omitted from `tools/list` (not seen as error). |
| Mint endpoint unavailable (mTLS rejected, comet-backend down, DB-fail-closed) | 503 (`mint_unavailable`) | `Couldn't authenticate Ollie's call to Opik. Retry in {seconds}s.` — surfaced when `/oauth/mint-user-api-key` returns 503 per §2.2.1. Status page entry. Distinct from `orchestrator_unavailable` (pod side) so triage routes correctly. |
| Daily quota exceeded | 429 | `Daily {tool} limit reached ({n}/{cap}). Resets in {hours}h. Upgrade: https://comet.com/billing` |
| Daily quota soft-warning (≥80% used) | 200 (success) | Tool returns normally; `metadata.quota_warning: {used: n, cap: c, remaining: c-n, reset_at: "<rfc3339>"}` set so hosts can surface a "you're approaching your limit" hint without changing behavior. Fires at every call once usage ≥ 80% of cap until reset. |
| Per-minute rate limit | 429 | `Rate limit exceeded. Retry in {seconds}s.` |
| Anthropic rate-limit | 503 | `Ollie is temporarily rate-limited. Retry in {seconds}s.` |
| Anthropic error | 502 | `Ollie's LLM provider returned an error: {detail}` |
| opik-backend 5xx | 502 | `Couldn't reach Opik. Status: {status}` |
| Pod cold-start timeout (>2 min) | 504 (`pod_cold_start_timeout`) | `Ollie didn't come up in time. Try again in a moment.` |
| Pod orchestrator unavailable (`/api/opik/ollie/compute` 5xx, codepanels down) | 503 (`orchestrator_unavailable`) | `Couldn't reach the pod orchestrator. Retry in {seconds}s.` — surfaced as a transient; status page entry. |
| Ollie session timeout | 504 | `Ollie didn't respond in time. Try a more focused question.` |
| Invalid `opik://` URI passed to `read` | 400 | `Unknown opik:// URI: {uri}` (raised as `ToolError` from `read_list/uri.py`) |
| Final message too long | 200 (with truncation marker) | `[truncated, ask Ollie to continue]` appended; `metadata.continuation_token` set |
| `thread_id` expired or unknown | 404 (`thread_expired`) | `Conversation expired. Retry without thread_id to start a fresh session.` |

## 2.14 Contract testing & schema source-of-truth

- **Opik REST schema** — `opik-backend`'s OpenAPI spec is the source of truth. `ollie-mcp` regenerates Pydantic models with `datamodel-code-generator` on every Opik release; the thin `opik_client.py` wraps `httpx.AsyncClient` over those models. CI fails the server if the regenerated models diff unsafely.
- **Ollie internal API** — `ollie-assist` publishes an OpenAPI doc; `ollie-mcp` generates a client and pact-tests against it nightly.
- **JWT claim schema** — Versioned. Schema doc lives in `comet-backend`, `ollie-mcp` has a parser pinned to a version, CI in `comet-backend` rejects breaking claim changes without a version bump.
- **MCP wire compatibility** — `ollie-mcp` runs the MCP conformance test suite in CI against current + previous minor spec versions. We **vendor the Python SDK's own `tests/conformance` fixtures** (`modelcontextprotocol/python-sdk` repo, `tests/`) under `ollie-mcp/tests/conformance/vendored/` and bump them on each SDK release rather than re-deriving frames by hand. Tasks-specific conformance: the SDK's `docs/experimental/tasks-server.md` example doubles as our reference shape for `CreateTaskResult` + `notifications/tasks/updated`.
- **MCP protocol substrate** — we sit on the SDK's low-level `Server` class (not a hand-rolled JSON-RPC dispatcher). `server.experimental.enable_tasks()` + `ctx.experimental.run_task(work)` is the substrate for the Tasks engine in `transport/tasks.py`; the file owns *our* lifecycle policy (TTL, audit logging, mTLS callback writes), the SDK owns the wire shape. This shrinks the surface area we have to ourselves keep spec-conformant.

## 2.15 Developer workflow

### 2.15.1 Local dev profile
`docker-compose.dev.yml` brings up: Opik backend, Ollie (with mocked Anthropic), `ollie-mcp`, Redis, and a **mock OAuth issuer** (Keycloak or a ~300-line FastAPI mock that mimics comet-backend's OAuth endpoints + JWKS). Devs run against this stack — no Comet React service needed.

### 2.15.2 Golden fixtures
`tests/conformance/fixtures/` contains curated MCP frame sets for: `initialize`, `tools/list`, `resources/list`, `tools/call ask_ollie` (Tasks path + blocking path + `thread_id` continuation), `tools/call add_test_suite_items` (with elicitation), `tools/call score` (no elicitation, direct write), `tools/call run_experiment`.

### 2.15.3 Quickstart
```
git clone https://github.com/comet-ml/ollie-mcp
cd ollie-mcp
make dev          # brings up docker-compose.dev.yml, runs server on :7777
make test         # unit + integration + conformance
make oauth-flow   # opens browser to local mock issuer for manual flow testing
```

Target: clone → first successful `tools/call` against local stack in **under 10 minutes**.

## 2.16 Testing strategy

- **Unit:** tool handlers (including `read` / `list` entity dispatch + compression), OAuth grant verification, quota policy, error taxonomy, JWKS verifier, mint cache. Mock HTTP via `respx` at the httpx-transport layer (same pattern as `ollie-assist`).
- **Integration:** full Streamable HTTP request/response with in-process Ollie mock and mock OAuth issuer; Redis-backed session map / event log.
- **Conformance:** MCP conformance test suite (vendored from the Python SDK's `tests/conformance` fixtures), run in CI on current + previous minor spec versions.
- **Manual smoke:** `make inspect` opens **MCP Inspector** (`@modelcontextprotocol/inspector`) against the local server — first-line tool for `tools/list`, `tools/call`, Tasks lifecycle, elicitation flows. Required check before merging any tool/prompt change.
- **Contract:** pact tests against Ollie + opik-backend (nightly).
- **E2E:** staging endpoint hit from a scripted host (synthetic MCP client + recorded host scenarios) on every deploy.
- **Manual host matrix:** before each release, manually test claude.ai, Claude Code, Cursor, VS Code Copilot.
- **Load:** k6 scenarios — sustained `tools/call`, burst `ask_ollie`, sustained resources, sustained cold-start pod warmups.
- **Security:** OWASP ZAP scan on each release; OAuth flow run through penetration-test playbook annually.

Coverage target: **90%** for `ollie-mcp` (security-critical).

## 2.17 Risks

| Risk | Mitigation |
|---|---|
| `ask_ollie` latency frustrates host LLMs | Tasks primitive returns in <2 s with an explanatory `model-immediate-response` meta; blocking-SSE fallback streams progress every 10 s. |
| OAuth complexity (DCR enterprise compat) | DCR + pre-registered + CIMD from day one; docs with screenshots for each. |
| Pod cold-start exceeds 2 min ceiling | Surface a 504 `pod_cold_start_timeout` to the host with retry guidance; alert on warmup p99 > 180 s; codepanels SLA tracked. |
| Self-hosted users without Anthropic key feel left out | Clear messaging at install (banner in docs); eight direct write tools + resources still ship; `opik-mcp` (TS) remains the polished offline path. |
| Surface drift vs Opik 2.0 | Generated client from OpenAPI; CI gate on schema diffs. |
| Anthropic cost runaway | Daily caps, alerts, internal SRE Grafana cost view, per-workspace overage throttling. |
| Prompt injection via trace data | Ollie's existing hardening; document residual risk; never expose untrusted trace content as direct system prompt to host. |
| MCP spec churn | Maintain two minor versions back; conformance harness in CI. |
| Token theft via host compromise | Short-lived JWTs (1 h), refresh rotation, reuse detection, scope minimization. |
| Pod helm-uninstall on idle invalidates open threads | `thread_expired` is a graceful failure mode; host retries without `thread_id`; pod orchestrator pushes a deprecation event so `ollie-mcp` can opportunistically warn. |
| `run_experiment` long-running in hosts without Tasks support | Fall back to the blocking-SSE path (host without `capabilities.experimental.tasks` and no `_meta.task.ttl` on the call); document expected wall-clock; surface `experiment_id` in initial response so the host can poll via `read("experiment", <id>)` after timeout. |
| Cross-team coordination (Opik core + Comet platform + legal + marketing) | Workstream owner table in §2.18 names a single accountable engineer per stream; weekly sync during the launch window. |

## 2.18 Workstreams

The launch is a **single GA**, not phased release gates. The nine workstreams below are parallelization seams. W1–W5 are the critical path; W6–W9 can land in parallel. (The previously specified W6 "Admin + audit UI" workstream is removed per ADR 0006; subsequent workstreams renumbered.)

| # | Workstream | Scope | Owner |
|---|---|---|---|
| **W1** | `ollie-mcp` core | New repo + Docker image; FastAPI scaffolding; MCP Streamable HTTP transport; Redis-backed session map + event log; MCP Tasks engine; tool dispatcher (pod vs REST); blocking-SSE fallback; capability negotiation at `initialize`. | Opik core |
| **W2** | OAuth AS + JWKS | `comet-backend` new routes: `/oauth/{authorize,token,register,revoke,mint-user-api-key}`, `/.well-known/{oauth-authorization-server,oauth-protected-resource,jwks.json}`. RS256 key rotation cron. Service-account JWT issuer. DCR + CIMD + pre-registered support. Consent UI (HTML-escaped, CSRF). DB migrations (`mcp_oauth_clients`, `mcp_authorization_codes`, `mcp_refresh_tokens`, `mcp_audit_log`, `mcp_mint_audit`). | Comet platform |
| **W3** | Pod-side trust | `ollie-assist` nginx Lua JWKS verifier (new `location /sessions` block with `bearer_jwt_verify`). Per-session `X-Opik-User-API-Key` capability (drops `OLLIE_USER_OPIK_API_KEY` env defaulting for service-account callers; keeps it for browser callers). Regression tests on both paths. | Opik core (Ollie sub-team) |
| **W4** | Tool surface | `ollie-mcp` implementations: `read`, `list` (entity registry covering 18 Opik entity types; adaptive compression), plus `score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `save_eval_item`, `run_experiment` (REST side). `opik_client.py` generated typed httpx client; CI step to regenerate on every Opik release. No MCP Resources per ADR 0004 D1 — reads are tools. | Opik core |
| **W5** | Pod discovery + cold-start | `ollie-mcp` calls `/api/opik/ollie/compute` with service JWT; readiness polling on pod `/health/ready` (1 s interval, 2-min cap); thundering-herd coordinator (`mcp:warmup:{workspace_id}` Redis lock + pub/sub); MCP Tasks shaping (immediate `CreateTaskResult`, `notifications/tasks/updated` cadence); blocking-SSE fallback; `Last-Event-ID` resumption; `tasks/get`-on-completion retrieval. `ask_ollie` tool wiring end-to-end including elicitation pass-through (gated on host `capabilities.elicitation`) and `navigate` allowlist. | Opik core |
| **W6** | `opik-mcp` (TS) polish | Opik 2.0 audit doc (`docs/superpowers/audits/2026-05-12-opik-mcp-toolset-audit.md`). Tool list cleanup. README repositions as scripted / CI / no-LLM path. Any in-budget core-tier fixes. | Opik core |
| **W7** | Self-hosted bundle | Docker-compose + helm chart updates (add `ollie-mcp`, conditional `ollie-assist` via profile). Operator docs (bring-your-own-Anthropic-key, no-LLM mode, `OPIK_MCP_ALLOW_UNAUTH` warnings, optional OAuth-into-your-own-IdP). Release-note entry. | Opik core (deploy sub-team) |
| **W8** | Discovery + verified-host | CIMD pre-registration entries for Anthropic, Cursor, Microsoft. `modelcontextprotocol.io` server registry listing. In-Opik "Connect" banner with per-host instructions. Docs hub page (`comet.com/docs/opik/mcp`). Blog post + lifecycle email + in-app changelog. DPA addendum with legal. | Marketing + Comet platform |
| **W9** | Observability + SLO | Prometheus metrics for all SLIs (§2.11.1). Internal SRE Grafana dashboards (§2.11.2). Alert rules (§2.11.3). Runbook (`docs/runbooks/ollie-mcp.md`). EU Anthropic routing for `ask_ollie` (workspace data-residency setting). | Opik core (SRE sub-team) |

**Critical path:** W1 → W4 / W5 in parallel → integration. W2 and W3 can start immediately and merge into the critical path on day one of integration. W6–W9 land in parallel anytime before the launch checklist (§1.14) is consumed.

**Dependencies inside W1–W5:**
- W4 (direct tools) depends on W1 (transport).
- W5 (pod discovery + cold-start) depends on W1 (transport) + W2 (service-account JWT issuer) + W3 (pod-side verifier).
- W4 and W5 can run in parallel after W1 lands.
- W2 and W3 are independent and can start immediately.

**Acceptance for GA:**
- All nine tools callable from a scripted MCP client with either OAuth or API-key auth.
- `ask_ollie` and `run_experiment` exercise both Tasks-primitive and blocking-SSE paths.
- `ask_ollie` honors `thread_id` continuation across a real cold-start.
- **`Last-Event-ID` resumption verified end-to-end**: scripted host opens an SSE stream, server kills its replica mid-stream (rolling deploy), host reconnects with the last-received event ID against a different replica, replay yields the missed events in order with no gaps; tested for both `ask_ollie` (Tasks path) and `run_experiment` (Tasks path).
- **Elicitation-absent host path verified**: scripted host advertising `capabilities.elicitation: false` calls `add_test_suite_items`, `score`, `comment`, `save_prompt_version`, `save_eval_item` — all succeed without elicitation, returning `metadata.confirmed_without_elicitation: true` per §2.5 elicitation fallback policy. The same host calls `ask_ollie` with a prompt that would normally trigger an Ollie-initiated write; Ollie's planner declines the write with the "host does not support elicitation; tell the user to upgrade their host" hint.
- Resources readable; SLOs from §2.11.1 met on staging load test; conformance harness green.
- Audit log rows present (including `thread_id` for `ask_ollie`); quotas enforced.
- OAuth `/oauth/revoke` (RFC 7009) invalidates cached mint keys within 60 s.
- Consent-screen XSS regression test green.
- RS256 key rotation verified end-to-end (rotate, confirm zero verification failures across `ollie-mcp` + pod verifiers).
- Subdomain-takeover synthetic monitor green.
- Manual host matrix smoke (claude.ai, Claude Code, Cursor, VS Code Copilot) all green per §2.11.5.
- All §1.14 launch checklist items complete.

## 2.19 References

**MCP spec & ecosystem**

- [OPIK-6439](https://comet-ml.atlassian.net/browse/OPIK-6439)
- [MCP 2026 roadmap](https://modelcontextprotocol.io/development/roadmap)
- [MCP Resources spec](https://modelcontextprotocol.info/docs/concepts/resources/)
- [MCP Transports spec (2025-03-26)](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)
- [MCP Tasks primitive (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)
- [SEP-991 — Client ID Metadata Documents](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/991)

**Strategic libraries we anchor on (production)**

- [`mcp` Python SDK (`modelcontextprotocol/python-sdk`)](https://github.com/modelcontextprotocol/python-sdk) — low-level `Server.streamable_http_app()`, `TokenVerifier` + `AuthSettings`, experimental Tasks (`docs/experimental/tasks-server.md`).
- [`sse-starlette`](https://github.com/sysid/sse-starlette) — production SSE for Starlette/FastAPI with multi-loop + graceful shutdown.
- [`httpx`](https://www.python-httpx.org/) + [`httpx-sse`](https://github.com/florimondmanca/httpx-sse) — pod→MCP upstream SSE consumption (`aconnect_sse`) with HTTP/2 keepalive.
- [`joserfc`](https://github.com/authlib/joserfc) — JWT/JWS/JWK verification; `KeySet.import_key_set` + callable-key resolver fits the dual-keypair `user-<n>` / `svc-<n>` model.
- [`redis-py`](https://github.com/redis/redis-py) (async, v6.4+) — sessions, event log, quota counters, warm-up coordinator lock, JWKS invalidation pub/sub.
- [`datamodel-code-generator`](https://github.com/koxudaxi/datamodel-code-generator) — build-time Pydantic models from Opik OpenAPI; CI regenerates on every Opik release.
- [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — env-driven config (lockstep with `ollie-assist`).
- [`structlog`](https://www.structlog.org/) — context-var bound structured logging.
- [OpenTelemetry FastAPI / httpx / Redis instrumentation](https://opentelemetry.io/docs/zero-code/python/) — auto-spans into the existing Opik OTEL collector.

**Strategic libraries we anchor on (dev / CI)**

- [`@modelcontextprotocol/inspector`](https://github.com/modelcontextprotocol/inspector) — manual smoke UI; `make inspect` invokes it on PRs touching the tool/resource/prompt surface.
- [`respx`](https://github.com/lundberg/respx) — httpx-transport mock (same as `ollie-assist`).
- [Anthropic SDK auth doc — OAuth resource servers](https://docs.anthropic.com/) — host-side OAuth conformance.
- [Anthropic — Custom Connectors via Remote MCP](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers)
- [Anthropic — Code execution with MCP (tool bloat)](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Atlassian — MCP Compression: Preventing tool bloat](https://www.atlassian.com/blog/developer/mcp-compression-preventing-tool-bloat-in-ai-agents)
- [Tool Search context-bloat analysis (candede.com)](https://www.candede.com/articles/claude-tool-search)
- [MCP — Tools vs Resources vs Prompts (Microsoft)](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/mcp-demystified-tools-vs-resources-vs-prompts-explained-simply/4508057)
- [Notion MCP](https://developers.notion.com/docs/mcp)
- [Linear MCP](https://linear.app/docs/mcp)
- [claude-code #52638 — DCR-only gap](https://github.com/anthropics/claude-code/issues/52638)
- [claude-code #38102 — DCR vs pre-registered clientId](https://github.com/anthropics/claude-code/issues/38102)
- [claude-code #26675 — pre-configured OAuth credentials](https://github.com/anthropics/claude-code/issues/26675)
- [claude-code #53253 — Slack MCP plugin OAuth fails](https://github.com/anthropics/claude-code/issues/53253)
- `opik/apps/opik-backend/src/main/java/com/comet/opik/infrastructure/auth/AuthFilter.java`, `RemoteAuthService.java` — existing auth pattern reused.
- `ollie-assist/src/ollie_assist/routers/dependencies.py`, `types/auth.py` — pre-launch pass-through auth that the pod-side JWT verifier sits in front of.
- `ollie-assist/src/ollie_assist/app.py:49` — unconditional Anthropic dependency that drives self-hosted Ollie's opt-in shape.
- `apps/opik-frontend/src/plugins/comet/useAssistantBackend.ts` — `/api/opik/ollie/compute` discovery + readiness polling pattern (lines 19–37, 168–201).
- `comet-ml/opik-mcp` — existing TS server, polished as the scripted / CI / no-LLM path.
