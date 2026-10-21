# 0008 — Stay on MCP python-sdk 1.x for now; the 2.x port is known

Status: proposed, 2026-09-25 (OPIK-8498)

## Decision

Keep `mcp[cli]>=1.27,<2`. Move to `mcp>=2.2,<3` in a PR of its own when
one of these happens:

- a host we support negotiates protocol `2026-07-28`;
- 1.x stops getting releases;
- we need a feature that only 2.x has.

That PR ports the changes in the table below, then passes three checks the
spike did not run: `make live`, `/dogfood` against Claude Code and Cursor,
and the Docker image behind the hosted proxy with an OAuth token that is
refreshed mid-session. It brings the spike's tests for the silent breaks:
the session dedup and the host bucket over a real `Client` in both protocol
eras. The token rebinding tests call the middleware with a fake context, so
only the hosted OAuth-refresh check covers that path.

The spike ran on `mcp` 2.2.0 on the local branch
`awkoy/OPIK-8498/mcp-v2-spike`. `make check` and `make e2e` pass there. The
schema snapshots and the tool-surface byte budget pass unchanged, so the
advertised surface costs the same. The port changes about 230 lines in
`src/` and about 300 in `tests/` and `scripts/`.

| 2.x change | What breaks, and how | What the port does |
|---|---|---|
| `FastMCP` is renamed `MCPServer`; transport settings leave `mcp.settings` | Import error; `mcp.settings.transport_security` raises | Rename. Pass the path and `TransportSecuritySettings` to `streamable_http_app()` |
| `_mcp_server`, `request_handlers` and `request_ctx` are removed | Silent, once the rename is done: each of the four handler swaps (instructions per session, token rebinding, `tools_listed`, skill resources) catches the `AttributeError` and installs nothing | The first three become one middleware each on the public `MCPServer.middleware`. Skill resources use `_lowlevel_server.add_request_handler`, which is still private |
| Middleware gets the serialised wire dict, not the result model | Silent: per-session instructions and the `tools_listed` tool count stop working | Read and rewrite the dict |
| A tool exception that is not a `ToolError` reaches the model as only "Error executing tool X" | Silent: the recovery hint in our errors is lost | A wrapper turns any exception except `MCPError` into a `ToolError` with its message. The module functions stay raw, so analytics still sees the real class |
| A new `ServerSession` proxy is built per request | Silent: `session_initialized` fires on every call; `tools_listed` and the host cache stop deduplicating | On `2025-11-25` sessions, key the three caches on the session's connection (`_connection`, private). On `2026-07-28` sessions every request gets a new connection too, so there is nothing per session to key on: the port PR picks a key (for example client info plus a hash of the bearer) or accepts one event per request |
| Protocol types use snake_case attributes (`client_info`, `is_error`, `input_schema`) | Silent where code uses `getattr(..., "clientInfo", None)`: every host buckets as `other` | Rename the reads. Test with a real `Client`, not a fake session |
| Types drop fields their protocol era does not define | On a `2025-11-25` session the top-level `ttlMs` and `cacheScope` of the skill resources are gone; the per-item `_meta` copy survives | Nothing. `2026-07-28` sessions keep both |
| `serverInfo.version` is empty unless it is passed | Hosts show no version | Pass `version=OPIK_MCP_VERSION` |
| `create_connected_server_and_client_session` is removed; `Client(server)` negotiates `2026-07-28` by default | Tests fail to import | Use `Client(mcp, mode="legacy")`, the era hosts use today, and `mode="auto"` where the two eras differ |
| Streamable HTTP defaults: 30-minute session idle timeout (1.x: none), 10,000 sessions, 4 MB request body | Silent: a hosted session idle for 30 minutes is closed, and the host has to reconnect | Decide the values in the port PR; the spike kept the defaults |
| New runtime dependencies: `httpx2`, `httpcore2`, `mcp-types`, `opentelemetry-api`, `truststore`; an OpenTelemetry middleware is on by default | Larger image; no spans unless a tracer provider is configured, and we configure none | Nothing |
| `ctx.info()` logging is deprecated, and on `2026-07-28` sessions it is dropped unless the request opts in | `MCPDeprecationWarning` on each tool call | Remove the `ctx.info` calls in the port |

On `2026-07-28` sessions every result also carries
`_meta["io.modelcontextprotocol/serverInfo"]`, about 60 bytes. It sits in
`_meta`, not in the content blocks. Whether a host passes it to the model is
unchecked; `/dogfood` in the port PR measures it (0001).

## Why

Staying on 1.x costs nothing today. 1.x is still released alongside 2.x
(1.30.0 and 2.2.0 both shipped on 2026-09-07). Until a host negotiates
`2026-07-28`, 2.x gives our users nothing new. Which hosts do is not checked
here.

Porting now has a cost. Six of the breaks above are silent, and one has no
fix yet: on `2026-07-28` sessions there is no per-session identity to
deduplicate analytics on. They cut the
hosted token refresh (OPIK-8252), error recovery and analytics. The existing
tests missed two of them, the session dedup and the host bucket, because they
use fake sessions. The spike added tests over a real `Client`, but only a
live host and the hosted image can show the port is safe.

## Enforced by

- The `<2` pin in `pyproject.toml`. CI runs `uv lock --check` and
  `uv sync --locked`, so the lock cannot drift to 2.x unnoticed.

## Log

- 2026-09-25: spike on `mcp` 2.2.0; stay on 1.x, port when a trigger above
  happens (OPIK-8498).
