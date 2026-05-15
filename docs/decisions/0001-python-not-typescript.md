# ADR 0001 — Python, not TypeScript

**Status:** Accepted
**Date:** 2026-05-12

## Context

`opik-mcp` is a new MCP server that needs to translate Ollie's pod-side SSE event vocabulary into MCP frames. The existing `opik-mcp` (TypeScript) ships as `npx opik-mcp` for the Cursor extension and serves as the scripted/CI/no-LLM path. Reusing TypeScript would feel like the obvious move.

## Decision

Build `opik-mcp` in **Python 3.13** with FastAPI + `modelcontextprotocol/python-sdk`.

## Rationale

The deciding factor is **code share with `ollie-assist`** — not throughput, not ecosystem, not hiring.

`opik-mcp`'s central job is translating Ollie's SSE event vocabulary (`thinking_delta`, `tool_call_start`, `tool_call_delta`, `confirm_required`, `navigate`, `compaction_*`, `message_end`) into MCP frames (`notifications/progress`, `notifications/tasks/updated`, `elicitation/create`). That vocabulary lives in `ollie-assist/src/ollie_assist/types/sse.py`.

In Python:
```python
from ollie_assist.types.sse import SessionEvent, ThinkingDelta, ToolCallStart, ConfirmRequired, Navigate
```

Event-shape drift becomes a CI failure, not a runtime bug. In any other language, every change to Ollie's emitter forces a hand-translation in two repos — and the kinds of bugs that show up are silent, async, and host-specific.

Same logic applies to:
- Auth context types (`WorkspaceContext`, multi-tenant key handling)
- The `httpx.AsyncClient` factory that talks to `opik-backend` — same factory `ollie-assist` already uses for `get_or_create_user_opik_client(workspace)`

## Second-order arguments

| Argument | Direction |
|---|---|
| One on-call language for the Ollie team | → Python |
| Same hire pool covers `ollie-mcp` and `ollie-assist` | → Python |
| `opik` Python SDK is a pinned dep in both repos (one PR per repo, not three) | → Python |
| `modelcontextprotocol/python-sdk` ships Streamable HTTP + OAuth resource-server helpers + experimental Tasks primitive | → Python |
| HTTP throughput (I/O-bound, SSE-heavy) | Either runtime fine |
| Memory footprint (~120 MB Python vs ~30 MB Go) | Irrelevant for always-warm Deployment |
| Cold start (~700 ms Python) | Irrelevant for always-warm Deployment |

## Why we keep `opik-mcp` (TypeScript)

The same argument cuts the other direction. `opik-mcp` (TS) is:
- A self-contained scripted / CI / no-LLM tool
- No shared event vocabulary
- No per-user pod
- No SSE translation
- A thin REST wrapper

TypeScript is the right call there, and we keep it. The two MCP servers serve different audiences — TS for scripted/CI, Python (this repo) for human-in-the-loop hosted.

## What this rules out

- Go runner-up (great runtime, no code share, gives up the deciding factor)
- Rust runner-up (same — no code share)
- TypeScript with hand-translated event types (silent async bugs)
- Single shared codebase for both `opik-mcp` (TS) and the new server (different shapes, different audiences)

## Notes

If `ask_ollie` were *not* in Phase 1 scope, the Python-vs-TS calculus would flip — without SSE translation, the code-share argument evaporates and TS polish becomes the right call. Product has confirmed `ask_ollie` is in Phase 1, so Python wins from day one.
