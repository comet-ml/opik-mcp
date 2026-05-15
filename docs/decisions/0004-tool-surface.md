# ADR 0004 — Tool surface: universal reads, narrow writes

**Status:** Accepted (implemented 2026-05-15 — D1 + D6 landed in `src/opik_mcp/read_list/` and `src/opik_mcp/instructions.py`; D2 narrow writes shipped earlier with `score`/`comment`; D7 doc softening tracked in design.md §1.16.)
**Date:** 2026-05-15
**Supersedes:** design.md §1.5 (tool surface) and §1.6 (resource surface).

## TL;DR (read this first if you're walking into the meeting)

The original design proposed **9 narrow tools + 12 `opik://` resources**. A counter-proposal landed: **6 universal tools** (`read`, `list`, `search`, `schema`, `write`, `ask_ollie`) — fail-and-bounce-back schema discovery, all entities behind one polymorphic surface.

After researching ~12 production hosted MCPs and the actual Ollie codebase, neither extreme wins. We're proposing a **hybrid**:

| Surface | Decision |
|---|---|
| **Reads** | Move from resources to **universal tools**: `read(entity, id)` + `list(entity, …)`. Drop the 12 `opik://` resources. |
| **Writes** | Keep **narrow per-entity tools** (8 of them). Reject `write(object, data)`. |
| **`ask_ollie`** | Keep unchanged. |
| **`schema()` tool** | Reject. Schemas already live in `tools/list`. |
| **`search(object, query)` tool** | Reject as proposed. Use `list(entity, name=…)` filters. Optional future: in-entity `search`/`jq` mirroring Ollie. |
| **Context injection** | Add MCP `instructions` field at `initialize` (user_email, opik_url, workspace, tool-selection guidance). New, additive. |

**Net surface:** ~10 tools (2 universal reads + 8 narrow writes + `ask_ollie`), zero resources, plus an instructions blob. Within Anthropic's "10–15 outcome-oriented tools + code mode" band.

## Context

Two competing positions on the table:

**Position A — Original design (design.md §1.5–1.6):**
- 8 narrow write tools: `score`, `comment`, `create_trace`, `create_span`, `add_test_suite_items`, `save_prompt_version`, `save_eval_item`, `run_experiment`
- 1 doorway tool: `ask_ollie`
- 12 URI-addressable resources for reads (`opik://traces/{id}`, etc.)
- Cited evidence: tool-count accuracy collapse, MCP spec "Resources for reading, Tools for doing"

**Position B — Counter-proposal:**
- 6 universal tools: `read(object, id?, name?)`, `list(object, parent_id?, limit?)`, `search(object, query?, filters?)`, `schema(object)`, `write(object, data)`, `ask_ollie`
- Argument: agents are robust to errors now; let `write()` fail with descriptive errors and a schema-in-error-response; <1k tokens total surface
- Argument: "this is how Ollie works"

Both make real points. The decision hinges on three empirical questions that we now have answers for.

## Empirical inputs

### 1. How Ollie actually works (verified against `ollie-assist/src/ollie_assist/`)

The teammate's claim "this is how Ollie works" is **half-true**. Verified from `ollie-assist/src/ollie_assist/agents/assistant/agent.py:115-159`:

- ✅ **Universal reads** — `read(entity_type, id)` covers 18 entity types in `ENTITY_REGISTRY`; `list(entity_type, name?, page?, size?)` covers 9 listable types. Both are universal-by-entity-type with one schema.
- ❌ **Universal writes** — Ollie does **not** have a `write(object, data)`. Writes are narrow: `add_ollie_catches_item`, `store_dataset_item`, `run_local_agent`, `run_local_test_suite`.
- ❌ **`search(object, query)`** — Ollie's `search` is regex/jq over a *cached entity blob* (in-entity grep), not cross-entity query. Different primitive.
- ❌ **`schema()` tool** — does not exist in Ollie. Entity types are listed in the `read` and `list` tool docstrings.
- ➕ Plus `opik_sdk` as a sandboxed code-mode escape hatch for the long tail.

The teammate's design universalizes Ollie's read pattern correctly but misrepresents its write pattern. Ollie's actual split is exactly the hybrid we're proposing here.

### 2. Production hosted MCP servers (12 surveyed)

GitHub, Linear, Notion, Atlassian Rovo, Sentry, Cloudflare, Stripe, Supabase, Vercel, HubSpot, Slack, Anthropic reference servers. Tool counts 5–51.

- **0 of 12** use a `write(object, data)` polymorphic-CRUD pattern. **Every** production write surface is narrow per-entity.
- Most expose 1–3 universal search/fetch tools alongside narrow tools, not instead of (Stripe: `search_stripe_resources`; Notion: `notion-search`/`notion-fetch`; Atlassian: `searchAtlassian`).
- **0 of 12** ship a separate `schema()` tool. Schemas are already advertised on `tools/list`.
- Cloudflare's 2-tool `search()`/`execute()` is the only radically universal design — and it requires the model to write **JavaScript code**, not JSON parameters. Different design space.

### 3. Anthropic's current published guidance (Feb 2026)

- "Writing Tools for Agents" — explicitly favors narrow, outcome-oriented naming with action verbs.
- "Code execution with MCP" — recommends "10–15 outcome-oriented tools + code mode for the long tail." Our ~10 tools + future `opik_sdk` lands inside this band.
- Tool Search Tool / Programmatic Tool Calling — Anthropic's answer to scaling is **search over narrow tools**, not polymorphism. Accuracy cliff cited at 30–50 tools, not at our scale.
- The "43% → 14%" stat in design.md §1.16 doesn't trace to a single benchmark; the closest are MCP-Universe (Salesforce) and Anthropic's own threshold guidance. **We should soften this citation in §1.16 D1.**

### 4. MCP client support for resources (the read-surface bug)

Worth flagging because it changed our position on reads:

- **Claude Code** does not surface MCP resources to the agent in tool-calling loops (they exist in the protocol but are not part of the LLM's decision context).
- **Cursor** renders resources as `@opik://...` mention completions only when the user explicitly types them.
- **claude.ai** support is in flux.

If ~95% of mid-session usage is reads and the read primitive is invisible to most agents, the original §1.6 design quietly bottoms out the value prop. This is the strongest single argument for moving reads into tools.

## Decisions

### D1. Reads → universal tools (move from resources to tools)

**Decision:** Replace the 12 `opik://` resources with two universal tools mirroring Ollie:

```
read(entity_type, id, max_tokens?)
list(entity_type, name?, page?, size?)
```

**Pros**
- Reads become visible to every MCP host (resources aren't surfaced by Claude Code, partial in Cursor/claude.ai).
- Matches Ollie's actual implementation 1:1 — share the `ENTITY_REGISTRY` codepath, no drift.
- One schema covers N entities — small `tools/list` footprint.
- Read flows compose with writes in one tool-calling loop instead of needing separate resource-fetch + tool-call cycles.

**Cons**
- Loses the `@opik://traces/abc` user-typed mention UX in Cursor (a real but minor UX regression for one client).
- Diverges from MCP spec rhetorical guidance ("Resources for reading, Tools for doing") — but the spec doesn't forbid tools-for-reads and the rhetorical guidance predates the resources-invisibility problem.
- Loses the zero-tool-list-cost property of resources — but each read tool is one entry, total cost is two entries.

**Mitigation:** Keep `opik://` URIs as a documented input format for `read(entity_type="...", id="opik://traces/abc")` so any future client that *does* surface resources can interop. Free; just a URI parser.

### D2. Writes → keep narrow (reject `write(object, data)`)

**Decision:** Keep the 8 narrow write tools as currently designed in §1.5.

**Pros**
- Tool names carry the strongest LLM signal (`create_trace` vs. `write({object: "trace", …})` — Anthropic's "Writing Tools for Agents" guidance).
- `inputSchema` per tool is tight and informative; LLM can validate intent against schema at planning time.
- Granular OAuth scopes (`mcp:write:traces`, `mcp:write:annotations`, etc.) map 1:1 to tools — a token without a given scope means the corresponding tools are omitted from `tools/list` (the LLM cannot see what it cannot call). Per-workspace admin scope disables are deferred per ADR 0006; scope enforcement at launch is token-level only.
- Zero round-trip tax: deterministic write is **one** `tools/call`.
- Per-tool quotas, SLOs, audit log labels stay clean.
- Universal precedent — 0 of 12 production servers ship polymorphic-CRUD writes.

**Cons**
- Larger `tools/list` than 1 universal write (8 entries vs. 1) — but still well below Anthropic's 30–50 cliff.
- Adding a new write entity is one new tool, not a new branch in `write`'s discriminator.

**Why `write(object, data)` was rejected (mechanics, not precedent)**

The teammate's argument is real — "let agents fail and bounce back" works in 2026. The cost just shows up in different places:

1. **`inputSchema` is either lying or bloated.** Honest schema = discriminated union of 8 entity schemas (larger than 8 separate tools). Lying schema = `{object: string, data: object}` with `additionalProperties: true` (forces a `schema()` round-trip on every write).
2. **Schema-first doubles round-trips on the hot path.** ~55% of mid-session calls are writes (§1.3 JTBD analysis). Narrow = 1 call. Universal = `schema()` + `write()` = 2 calls, every session, or pay it via error-recovery on a miss.
3. **Error-recovery isn't free.** A miss costs 1 failed call + ~500–1500 tokens of schema in the error body + 1 retry. Forever, not just at session start.
4. **Granular OAuth scopes stop mapping cleanly.** With narrow tools, scope enforcement is structural (tool omitted from `tools/list`). With one `write`, the LLM sees the tool advertised but errors on disallowed `object` values — worse failure mode.
5. **Selection cost relocates, doesn't disappear.** LLM still picks `object: "trace"` correctly. The decision moved from the tool name (highest-signal token) into a string parameter in JSON.
6. **Observability/quotas get a label multiplier.** Per-tool SLOs and quotas become `(tool, object)` pairs.

When universal writes *would* win: 100+ entity types (Cloudflare regime), no granular scopes, pure CRUD with no per-tool semantics. None of those apply to us.

### D3. Reject `schema()` as a separate tool

**Decision:** No `schema()` tool. Schemas come from `tools/list` (the protocol already advertises full JSON Schema per tool).

**Pros**
- One source of truth — every host already fetches `tools/list` once per session and caches it.
- No mandatory warm-up round-trip per write.
- Future schema changes propagate via the existing `tools/list_changed` notification.

**Cons**
- None identified. The teammate's `schema()` proposal exists only to support `write(object, data)`, which we're rejecting.

### D4. Reject `search(object, query)` as proposed; use list filters

**Decision:** No top-level `search(object, query)` tool. Cross-entity discovery uses `list(entity_type, name=…)`.

**Rationale:** Ollie's actual `search` is in-entity regex/jq, not cross-entity query — the teammate mis-cited it. The functionality the teammate wanted (find entities matching a query) is already covered by `list(entity, name=…, page=…)` with the existing Opik backend search params.

**Optional future:** If we later need Ollie-style in-entity grep, add `jq(entity_key, expression)` mirroring Ollie's `JqTool` exactly. Not on the launch path.

### D5. `ask_ollie` unchanged

**Decision:** Keep `ask_ollie` exactly as designed in §1.5. No changes.

The doorway tool for investigate / synthesize / multi-step work. Reuses thread_id, Tasks primitive, SSE proxying — all unchanged.

**Phase 1 keep-alive stand-in.** The MCP Tasks primitive (SEP-2663) is `experimental` with no production-host support at launch (June 2026 RC at earliest). Until hosts ship it, `ask_ollie` keeps tool-call timeouts alive entirely via `notifications/progress`: one tick per pod SSE event plus a watchdog heartbeat (`OPIK_MCP_HEARTBEAT_INTERVAL_S`, default 15 s) during pod silence. See design.md §2.5 "Phase 1 keep-alive" for the mechanics; when Tasks lands the heartbeat is retired in favor of `notifications/tasks/updated`.

### D6. Add `instructions` field at `initialize`

**Decision:** Populate the MCP `InitializeResult.instructions` field with per-session context, injected by the host LLM as system-prompt-like guidance.

Content:

```
You're connected to Opik (Comet's LLM observability platform) as {user_email}
in workspace "{workspace_name}". The Opik UI is at {opik_url}.

Tool selection:
- Reads (read, list): use for any "show me X" or "what is Y" — these are cheap.
- Direct writes (score, comment, create_trace, create_span, add_test_suite_items,
  save_prompt_version, save_eval_item, run_experiment): use when the user's
  intent is concrete and well-defined. Skip ask_ollie.
- ask_ollie: use for investigative questions ("why is X failing?"), cross-entity
  synthesis ("compare experiments A and B"), or when authoring/instrumentation
  requires Opik domain expertise.

Today's date is {date}. Active project context (if any): {project_name}.
```

**Pros**
- GitHub MCP's published data: **+25pp workflow adherence** with dynamic per-toolset instructions; **+60pp on smaller models** (GPT-5-Mini class).
- Single place for cross-cutting context (user, workspace, URL, tool-selection guidance) — avoids repeating it across N tool descriptions.
- Token-efficient — the instructions blob is ~200 tokens injected once, vs. inlining the same context into every tool description.
- Becomes the primary signal when Tool Search Tool is active (only instructions + search tool visible initially).

**Cons**
- Spec marks it optional and non-mandatory for clients — Claude Code, VSCode, Goose, Cursor do inject it today, but we shouldn't *depend* on it. Every tool must remain usable from its own description alone.
- Maintenance cost — instructions evolve as the tool surface evolves.
- "No instructions are better than poorly written instructions" (MCP maintainer). We need to write this carefully and test it.

### D7. Soften the "43% → 14%" citation in design.md §1.16 D1

**Decision:** Replace the unsupported "43% → 14% accuracy collapse" sentence with citations to:
- MCP-Universe (Salesforce AI Research, 2025) — best model 43.72% overall on 231 tasks across 11 MCP servers
- Anthropic's published threshold — "Claude's ability to correctly pick the right tool degrades significantly once you exceed 30–50 available tools"
- Anthropic Tool Search data — Opus 4.5 79.5% → 88.1% with Tool Search on

The original framing still holds (small narrow surface beats large surface), just with sourceable numbers.

## What this changes in the existing docs

| File | Change |
|---|---|
| `docs/design.md` §1.5 | Add `read` and `list` to the tool table. Update "Why no polymorphic CRUD tools" paragraph to reference this ADR. |
| `docs/design.md` §1.6 | Replace "Resource surface" section with "Read surface" describing `read`/`list` and the `ENTITY_REGISTRY`. Note that `opik://` URIs remain as valid `id` inputs for forward-compat. |
| `docs/design.md` §1.16 D1 | Soften the "43% → 14%" citation per D7. |
| `docs/design.md` §1.16 D3 | Update — reads are no longer Resources; cite the client-support evidence. |
| `docs/design.md` §1.8 | Scope `mcp:read` now covers `read`/`list` tools, not resource fetches. |
| `docs/design.md` (new §) | Add a subsection on `InitializeResult.instructions` content (per D6). |
| `docs/architecture.md` | Update tool registry diagram. |

## Open questions for the meeting

1. **`@opik://` mention UX in Cursor — do we care?** Loss is real but small. Could be addressed later by re-adding a thin resource layer that delegates to the same `read` tool internals. Vote: skip for launch.
2. **Where should the instructions blob live in code?** Static template in `opik-mcp/src/opik_mcp/instructions.py` rendered per-session with user/workspace context, or pulled from a config file? Recommend code-rendered template — easier to test.
3. **Do we ship an `execute_python` / `opik_sdk` code-mode tool at launch or after?** Design.md §1.15 currently says "out of scope." Anthropic guidance says it's the recommended long-tail escape hatch. Reaffirm out-of-scope-for-launch — sandboxing a Python runtime is a separate workstream from the MCP tool surface, not a hosting/auth dependency. Revisit as a post-launch addition to the Phase 1 surface once requirements are sharpened.
4. **Should `read` accept an `opik://` URI as `id`?** Recommend yes (free, forward-compat with any future resource-aware client). Implementation = a URI parser in front of the existing dispatcher.
5. **Should `list` support cross-entity discovery via a `parent_id` filter (the teammate's `list(object, parent_id?)` shape)?** Opik's REST API already supports project-scoped traces/spans queries; `list(entity_type="trace", project_id=...)` is the natural shape. Recommend yes.

## What this rules out

- Polymorphic-CRUD writes (`write(object, data)` and `schema()`) — rejected on mechanical grounds, not just precedent.
- Standalone `search(object, query)` as a top-level tool — covered by `list` filters.
- Resources as the primary read surface — moved to tools.
- Inlining user/workspace context into every tool description — uses `instructions` field instead.

## Notes

The teammate's broader point — "MCP was designed before agents were ubiquitous; failures are cheap recovery, not catastrophes" — is correct and worth internalizing across the whole design. We do apply it: `ask_ollie` is the agent doorway, error responses are designed to be machine-readable and self-teaching, no tool requires the LLM to know more state than it can see. The specific `write(object, data)` proposal just happens to be the place where that principle and the structural cost of granular OAuth scopes / latency-sensitive writes pull in opposite directions, and the structural costs win. The teammate's principle survives in D1 (universal reads), D6 (instructions field), and how we shape error responses on every tool.
