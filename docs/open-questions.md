# Open questions

Tracked questions that aren't blockers but need answers before or during Phase 1 build.

## Q1 — One API key or two?

**Status:** Recommendation pending product confirmation.

`comet-backend` and `opik-backend` share the same API key DB on cloud Comet — the user's Comet API key *is* their Opik API key. So `OPIK_API_KEY` alone is sufficient for all three upstreams.

But self-hosted Comet installs can in principle have separate key stores.

**Recommendation:** Standardize on `OPIK_API_KEY` as the single env var. Document that on self-hosted with separate stores, the user needs the *Opik* key (since `comet-backend` is optional in those installs anyway — Ollie may not be deployed).

Matches existing `extensions/cursor/src/mcp/mcpService.ts` pattern.

## Q2 — Workspace switching UX

**Status:** Open.

Each host install is bound to one workspace via `COMET_WORKSPACE`. Switching workspaces means:

- Option A: Edit the host config (`~/.claude.json`, `mcp.json`, etc.) and restart
- Option B: Run a second `opik-mcp` instance with a different config + register it as a separate MCP server in the host
- Option C: Add a `switch_workspace` tool that mutates in-process state

A and B are reasonable for power users. C is friendlier but requires resource list re-emission and may surprise hosts that cache the tool list.

**Recommendation:** Ship A as the documented path in Phase 1. Revisit C in Phase 2 once we see how often users actually switch.

## Q3 — Self-hosted users without `ollie-assist`

**Status:** Decided — partial surface.

Self-hosted Comet installs without `ollie-assist` deployed:
- `GET /opik/ollie/compute-api-key` returns `{computeURL: "", enabled: false}`
- `opik-mcp` reads `enabled: false` at `initialize` time and omits `ask_ollie` + `run_experiment` from the advertised tool list
- All 9 other tools (`read`, `list`, `score`, `comment`, `add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `save_eval_item`) work normally against `opik-backend`

This avoids advertising tools the user can't actually call.

## Q4 — First-API-key assumption in pod provisioning

**Status:** Open — needs documentation, possibly a fix in `comet-backend`.

`comet-backend.provisionOlliePod()` seeds the new pod with the user's **first** Comet API key as `OLLIE_USER_OPIK_API_KEY`. If the user has multiple keys and is using a non-first one for `opik-mcp`, the pod will make `opik-backend` calls as a different key than the user might expect.

In practice this doesn't break anything because all keys for the same user resolve to the same identity, but it's a confusing implicit binding.

**Options:**
- Document "use one key everywhere" in Phase 1 install instructions
- Add an optional `?api_key=...` query param to `/ollie/compute-api-key` so the MCP server can pin the pod to a specific key

Defer the fix; document for now.

## Q5 — MCP Tasks primitive host coverage

**Status:** Open — verify in Week-1 PoC.

The MCP Tasks primitive (Streamable HTTP + `notifications/tasks/updated` + `tasks/get`) is marked `experimental` in spec rev 2025-11-25. Host support matrix is unverified:

| Host | Tasks support | Fallback needed? |
|---|---|---|
| Claude Code | Likely yes | TBD |
| Cursor | Unknown | TBD |
| VS Code Copilot | Unknown | TBD |
| MCP Inspector | Likely yes (reference impl) | TBD |
| claude.ai (web) | N/A — only remote MCP | Phase 2 |

PoC Day 6-8 explicitly tests this. If a host silently ignores `capabilities.experimental.tasks`, we need blocking-SSE with `notifications/progress` heartbeats.

**Action:** Build the conformance suite in `tests/conformance/` to gate releases.

## Q6 — Cold-start UX when pod is warm

**Status:** Open — minor UX detail.

When the pod is already warm (returned `{enabled: true}` from `/ollie/compute-api-key` *and* `/health/ready` returns 200 on the first poll), there's no perceptible cold start. But we still send a `CreateTaskResult` immediately and one `notifications/tasks/updated{status: "Connecting to Ollie..."}` before the SSE stream opens.

That's ~50ms of "Connecting..." flicker. Acceptable, but worth noting.

**Recommendation:** Don't optimize. The flicker is honest about what's happening.

## Q7 — Distribution: PyPI + Docker, or just PyPI?

**Status:** Open — Phase 1 only.

Phase 1 ships as a local Python process. Two reasonable distribution paths:

- **`uvx opik-mcp`** — single command, no Python env management, `uv` handles isolation. Friendliest.
- **`docker run ghcr.io/comet-ml/opik-mcp:<tag>`** — works for users who don't have `uv` or want fixed dependencies. Heavier.

Both are easy; the question is what to *recommend* in install docs.

**Recommendation:** `uvx` is the primary install path. Docker is the "if you have constraints" path, documented but not the headline. Phase 2 hosted MCP supersedes both.

## Q8 — SSE event vocabulary stability

**Status:** Open — risk to flag.

Ollie's SSE event types (`thinking_delta`, `tool_call_start/delta`, `confirm_required`, `navigate`, `compaction_*`, `message_end`) are defined in `ollie-assist/src/ollie_assist/types/sse.py`. The `opik-mcp` translation layer depends on these shapes.

Phase 1 picks up these types via a shared package (`ollie-assist-types` extracted as a PyPI dep — ADR-0001 rationale).

**Risk:** If Ollie evolves the vocabulary (e.g. adds `streaming_tool_result_v2`), `opik-mcp` needs a coordinated release. Same-team ownership mitigates but doesn't eliminate.

**Mitigation:**
- Pin the shared types package by exact version
- Run `opik-mcp` CI against `ollie-assist` `HEAD` daily to catch drift
- Cover every event type in `tests/translation/test_sse_to_mcp.py`

## Q9 — Workspace context in tool descriptions

**Status:** Open — UX polish.

Each tool description currently says "for the current workspace." Should we interpolate the actual workspace name (`COMET_WORKSPACE`) into the description so the host shows e.g. "Run an experiment in `my-workspace`"?

**Pro:** Reduces user confusion when multiple `opik-mcp` instances run.
**Con:** Some hosts cache tool descriptions; rotating them costs handshake bandwidth.

Defer; revisit after seeing how the host UIs render tool catalogs.
