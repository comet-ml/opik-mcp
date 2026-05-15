# opik-mcp

> **Looking for the TypeScript v2 server?** It still ships on npm as `opik-mcp@^2`
> (`npx -y opik-mcp`) and the source is preserved at
> [`legacy/typescript/`](./legacy/typescript/). See
> [`legacy/typescript/DEPRECATED.md`](./legacy/typescript/DEPRECATED.md) for
> the support policy.

Hosted Model Context Protocol server for **Opik** + **Ollie**. Exposes a curated, OAuth-secured surface to external AI hosts (Claude Code, Cursor, claude.ai, VS Code Copilot, MCP Inspector).

| | |
|---|---|
| **Status** | Phase 1 in planning — auth-flow research done, PR opened against `comet-backend` |
| **Jira** | [OPIK-6439](https://www.atlassian.com/) — _Improve Opik MCP server and include Ollie tool_ |
| **Team brief** | [`docs/team-brief.md`](./docs/team-brief.md) — shareable narrative + team-by-team build list |
| **Design doc** | [`docs/design.md`](./docs/design.md) — engineering source-of-truth (sequence diagrams, schemas, full task list) |
| **Notion mirror** | [Notion page](https://www.notion.so/35f7124010a381ca82a5df67d7474313) — same team brief, easier to share |

---

## What is this

`opik-mcp` is a **Python 3.13 / FastAPI** MCP server that:

- Exposes **11 outcome-oriented tools** — `ask_ollie` (LLM-gated) + `read` / `list` (universal reads over 8 Phase-1 entities) + 8 deterministic write tools
- Speaks **MCP Streamable HTTP** (`modelcontextprotocol/python-sdk ^1.12`)
- Handles **Ollie pod cold-start** via the MCP Tasks primitive with a blocking-SSE fallback
- Translates Ollie's pod-side SSE event vocabulary into MCP frames (`notifications/progress`, `notifications/tasks/updated`, `elicitation/create`)

Two delivery modes:

- **Phase 1** — local install, API-key auth, single-user-per-pod, Claude Code / Cursor / VS Code Copilot
- **Phase 2** — hosted at `https://www.comet.com/api/v1/mcp`, OAuth 2.1 + PKCE + DCR + CIMD, multi-tenant pods, all four hosts including claude.ai

This repo ships both. Phase 1 first.

---

## The 11 tools

| # | Tool | Bucket | Dispatch | Phase 1? |
|---|---|---|---|---|
| 1 | `ask_ollie` | Investigate | Ollie pod | ✅ (cloud Comet users only) |
| 2 | `read` | Read | `opik-backend` REST (composite for trace+spans, prompt+versions) | ✅ |
| 3 | `list` | Read | `opik-backend` REST | ✅ |
| 4 | `score` | Annotate | `opik-backend` REST | ✅ |
| 5 | `comment` | Annotate | `opik-backend` REST | ✅ |
| 6 | `add_test_suite_items` | Curate | `opik-backend` REST | ⬜ |
| 7 | `save_prompt_version` | Curate | `opik-backend` REST | ⬜ |
| 8 | `create_trace` | Author | `opik-backend` REST | ⬜ |
| 9 | `create_span` | Author | `opik-backend` REST | ⬜ |
| 10 | `run_experiment` | Iterate | Ollie pod | ⬜ |
| 11 | `save_eval_item` | Iterate | `opik-backend` REST | ⬜ |

Reads use the **two universal `read` / `list` tools** keyed on entity type — same `ENTITY_REGISTRY` codepath as `ollie-assist`. Phase 1 covers `project`, `trace`, `span`, `test_suite`, `experiment`, `prompt`, `test_suite_item`, `prompt_version`. `opik://` URIs are still accepted as `id` input for forward-compat. The MCP `resources` primitive is not published — see [ADR 0004](./docs/decisions/0004-tool-surface.md) for rationale.

---

## Phase 1 status

| Item | Status |
|---|---|
| Auth flow research | ✅ done — see [`docs/auth-flow.md`](./docs/auth-flow.md) |
| `comet-backend` patch (API-key-callable pod discovery) | 🟡 PR open — [comet-ml/comet-backend#5555](https://github.com/comet-ml/comet-backend/pull/5555) |
| Vertical-slice PoC (hello-world MCP) | ⬜ not started |
| `ask_ollie` over local MCP (no Tasks) | ⬜ not started |
| `ask_ollie` over local MCP (Tasks primitive) | ⬜ not started |
| `read` / `list` universal-read tools (8 Phase-1 entities) | ✅ done |
| `score` / `comment` direct write tools | ✅ done |
| Remaining write tools (`add_test_suite_items`, `save_prompt_version`, `create_trace`, `create_span`, `save_eval_item`, `run_experiment`) | ⬜ not started |
| `InitializeResult.instructions` per-session context (ADR 0004 D6) | ✅ done |
| Distribution package (`uvx opik-mcp`) | ⬜ not started |

See [`docs/phase-1.md`](./docs/phase-1.md) for the week-1 build order.

---

## Repo layout (planned)

```
opik-mcp/
├── README.md                 ← you are here
├── docs/
│   ├── design.md             ← full engineering design (canonical, ~1.4k lines)
│   ├── team-brief.md         ← shareable narrative + team-by-team build list
│   ├── architecture.md       ← stack, tools, resources, dispatch model (Phase 1 lens)
│   ├── phase-1.md            ← Phase 1 scope, build order, week-1 PoC plan
│   ├── auth-flow.md          ← Comet/Opik auth path verified against code
│   ├── open-questions.md     ← unresolved decisions
│   ├── install/              ← per-host MCP config snippets (TBD)
│   └── decisions/            ← ADRs (Python vs TS, separate repo, etc.)
├── src/
│   └── opik_mcp/             ← Python package (TBD)
├── tests/
├── pyproject.toml            ← TBD
└── Makefile
```

---

## Getting started (Phase 1, TBD)

Once the PoC lands:

```bash
# Cloud Comet users
export OPIK_API_KEY=<your-key>
export COMET_WORKSPACE=<workspace-name>
uvx opik-mcp

# Self-hosted Opik users
export OPIK_API_KEY=<your-key>
export OPIK_URL_OVERRIDE=https://your-opik.example.com
uvx opik-mcp
# Note: ask_ollie and run_experiment omitted from tools/list unless you have ollie-assist deployed
```

Per-host MCP config snippets will be in [`docs/install/`](./docs/install/) once the package ships.

---

## Try `ask_ollie` against `dev.comet.com`

The Day-3-to-5 PoC milestone runs `ask_ollie` end-to-end against `dev.comet.com` (where the `/opik/ollie/compute-api-key` backend fix is deployed).

```bash
export OPIK_API_KEY=<your-key>
export COMET_WORKSPACE=<workspace-name>
export COMET_URL_OVERRIDE=https://dev.comet.com

make install        # one-time
make run-dev        # uvicorn on 127.0.0.1:8080 with --reload + DEBUG logs
make inspect        # MCP Inspector in another shell
```

In the Inspector: connect to `http://127.0.0.1:8080/mcp` with header `Authorization: Bearer dev-token-123`. The `ask_ollie` tool will appear in the tool list — invoke it with `query: "How many traces did I create today?"` and watch the progress notifications during pod warmup.

**YOLO mode (always on).** Writes Ollie performs mid-stream (scores, comments, test-suite items, prompts, etc.) auto-execute without a per-action user confirmation. The pod's `confirm_required` SSE event is acknowledged with `decision="yes"` in-band, and a JSON audit row is emitted on the dedicated `opik_mcp.audit` Python logger (`event: "ollie_write_auto_approved"`). Configure that logger like any other (`logging.getLogger("opik_mcp.audit")`) to route audit lines to a file, journald, etc. Rationale and Phase-2 persistence path: [`docs/decisions/0005-ask-ollie-yolo-mode.md`](./docs/decisions/0005-ask-ollie-yolo-mode.md).

### Privacy & telemetry

`opik-mcp` sends anonymous product-analytics events to `stats.comet.com` so the team can measure adoption and reliability. No tool input prose (queries, comments, scores, page context) is ever sent — only event type, timing buckets, and low-cardinality structural properties. See `docs/superpowers/plans/2026-05-15-mcp-analytics.md` §4.5 for the full "never sent" list.

To disable, set `OPIK_MCP_ANALYTICS_ENABLED=false`.

Configuration env vars:

| Variable | Default | Notes |
|---|---|---|
| `OPIK_API_KEY` | — | required to call `ask_ollie` |
| `COMET_WORKSPACE` | — | required to call `ask_ollie` |
| `COMET_URL_OVERRIDE` | `https://www.comet.com` | set to `https://dev.comet.com` for the PoC |
| `OPIK_URL` | derived from `COMET_URL_OVERRIDE` | override for non-standard Opik deployments where Opik lives on a different host/path than the Comet UI |
| `OPIK_DEFAULT_PROJECT_NAME` | _unset_ | Name of your default project. When set, the session's `instructions` blob tells the LLM to pass it as `project_name` on every tool call unless the user names a different project. Matches the Python/TS SDKs, which expose project by name only. |
| `OPIK_MCP_DEV_TOKEN` | `dev-token-123` | bearer the MCP transport requires |
| `OPIK_MCP_POD_READY_TIMEOUT_S` | `120` | cold-start poll cap |
| `OPIK_MCP_POD_READY_INTERVAL_S` | `2` | cold-start poll interval |
| `OPIK_MCP_HEARTBEAT_INTERVAL_S` | `15.0` | watchdog cadence — see "Long-running Ollie operations" below |
| `OPIK_MCP_HOST` / `_PORT` | `127.0.0.1` / `8080` | uvicorn bind |
| `OPIK_MCP_RELOAD` | _unset_ | `1` to enable `--reload` |
| `OPIK_MCP_LOG_LEVEL` | `INFO` | stderr logger threshold |
| `OPIK_MCP_ANALYTICS_ENABLED` | `true` | set to `false` to disable all telemetry |
| `OPIK_MCP_ANALYTICS_URL` | `https://stats.comet.com/notify/event/` | analytics endpoint (override for staging) |
| `OPIK_MCP_ANALYTICS_ENVIRONMENT` | `prod` | environment tag on every event (`prod` / `staging` / `dev`) |
| `OPIK_MCP_ANALYTICS_CONNECT_TIMEOUT_S` | `5.0` | analytics HTTP connect timeout (seconds) |
| `OPIK_MCP_ANALYTICS_TOTAL_TIMEOUT_S` | `10.0` | analytics HTTP total request timeout (seconds) |

### Long-running Ollie operations

Some Ollie turns take a while — a Python SDK roundtrip against `opik-backend`, a multi-step `add_test_suite_items` flow, a `run_experiment` cold start. While the pod is busy and the SSE stream is silent, MCP hosts can time out the in-flight `tools/call` and return nothing to the user.

opik-mcp keeps the call alive two ways:

1. **One `notifications/progress` per pod SSE event.** Every `thinking_delta`, `tool_call_start`, etc. produces a progress tick — these are what the MCP spec [allows hosts to reset their timeout clock on](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle), unlike info-level log messages.
2. **A watchdog heartbeat every `OPIK_MCP_HEARTBEAT_INTERVAL_S` seconds (default 15s).** When the pod is silent, an internal task emits a progress tick with `message="streaming"` so hosts that follow the spec see a heartbeat well under their default timeout.

**Known host limitations.** The spec word is "MAY" — not every host resets on progress. As of writing:

- **Cursor** has a hard 60s tool-call timeout that does not reset on progress notifications ([bug report](https://forum.cursor.com/t/mcp-tool-timeout/74465)). Operations that take longer than ~60s will fail on Cursor regardless of heartbeat. Tune your prompt to keep `ask_ollie` turns short on that host.
- **MCP Inspector** has a `MAX_TOTAL_TIMEOUT` (default 60s) that bounds the *total* tool-call duration. Set it to a larger value via the Inspector UI for long operations.
- **Claude Code** has no documented tool-call timeout; the heartbeat keeps it indefinitely streaming until `message_end`.

If you need to debug a stuck call, set `OPIK_MCP_LOG_LEVEL=DEBUG` — heartbeat failures (typically host disconnects) are logged on `opik_mcp.ask_ollie` at debug level so they don't tear down the stream.

Run the live end-to-end against `dev.comet.com` (default `make check` skips it):

```bash
RUN_LIVE_DEV_COMET=1 OPIK_API_KEY=... COMET_WORKSPACE=... COMET_URL_OVERRIDE=https://dev.comet.com make test-live
```

> First-call note: `provisionOlliePod()` seeds the pod with your *first* Comet API key. If you authenticate with a different one, opik-backend calls *from inside the pod* will use the seeded key — use one key everywhere for the PoC (open question Q4).

---

## Why Python (not TypeScript)

Short version: code-share with `ollie-assist` for the SSE event vocabulary. The translator imports `from ollie_assist.types.sse import SessionEvent, ThinkingDelta, ToolCallStart, ConfirmRequired, Navigate` — event-shape drift becomes a CI failure, not a runtime bug. In any other language, every change to Ollie's emitter forces a hand-translation in two repos.

Full reasoning: see the team brief's "Why Python (not TypeScript)" section and [`docs/decisions/0001-python-not-typescript.md`](./docs/decisions/0001-python-not-typescript.md).

---

## Why a separate repo (not inside `ollie-assist`)

Three reasons, all hard:

1. **Per-user Ollie pods have no stable external URL.** External MCP hosts register one URL in config and can't do per-call workspace→pod discovery.
2. **Cold start is up to two minutes.** MCP hosts time out at ~30 s. We need an always-warm service that returns `CreateTaskResult` in <2 s.
3. **The per-user pod has no OAuth and no JWT verifier.** Wrong tier to host the public endpoint on.

Full reasoning: see the team brief's "Why this is a separate `ollie-mcp` repo" section and [`docs/decisions/0002-separate-repo.md`](./docs/decisions/0002-separate-repo.md).
