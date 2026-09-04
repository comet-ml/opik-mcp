# Opik MCP Server

**The official Model Context Protocol (MCP) server for [Opik](https://github.com/comet-ml/opik), the open-source LLM observability and evaluation platform, built by [Comet](https://www.comet.com).**
Plug your AI host (Claude Code, Cursor, VS Code Copilot, MCP Inspector) directly
into your Opik workspace: read traces, log scores, and save prompt versions, all
from the chat.

Built for LLM engineers who already run Opik and want to drive it from the same
AI assistant they code with.

> **Migrating from the old `npx opik-mcp`?** The TypeScript server is deprecated
> and sunsets on **2026-11-15**. Swap `npx -y opik-mcp` for **`uvx opik-mcp@latest`**
> in your MCP client config. Full guide: [`legacy/typescript/MIGRATION.md`](./legacy/typescript/MIGRATION.md).

```
You:    "Which traces in project 'demo' failed today?"
Claude: → list(entity_type="trace", project_name="demo") → "Three traces failed…"

You:    "Score trace 7f2e… 0.9 on helpfulness with reason 'great recovery'."
Claude: → write(score.create) → done
```

---

## Install

`opik-mcp` is a Python package (requires Python 3.13+). The recommended way to
run it is `uvx`, which fetches and runs the latest published version on demand —
no global install, no virtualenv juggling.

Install [`uv`](https://docs.astral.sh/uv/) once:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# or: brew install uv
```

You'll need two things from your Opik workspace:

- **`OPIK_API_KEY`** — get it from [`comet.com/api/my/settings/`](https://www.comet.com/api/my/settings/).
- **`OPIK_WORKSPACE`** — your workspace name (lowercase, as it appears in the URL). E.g. `https://www.comet.com/acme-ai/...` → `OPIK_WORKSPACE=acme-ai`. `COMET_WORKSPACE` is accepted as a deprecated alias.

> **Cloud, with an API key: set it unless your account default is the one you
> want.** Left out, the server sends `default`, which Comet resolves to your
> account's default workspace. That works, but if you actually work in a named
> workspace you will be pointed at a different one with nothing to tell you —
> your reads come back from the wrong place rather than failing.
>
> **Cloud, over OAuth: leave it unset.** The workspace comes from the token you
> authorized, and the server ignores this setting entirely.
>
> **Local / open source: leave it unset.** Open source Opik has a single
> workspace named `default` and no way to create others, which is exactly what
> the fallback gives you.
>
> **Self-hosted Comet: set it.** Unlike open source, these deployments have real
> named workspaces, and the same silent-wrong-workspace risk applies.
>
> Whichever applies, make sure the value is actually substituted. Snippets in
> the wild ship placeholders like `<your-workspace>` or `${input:OPIK_WORKSPACE}`;
> pasted as-is, those are not workspace names. The server now refuses them
> outright rather than letting the backend answer with an auth error that
> explains nothing.

### Claude Code

Add the server with one command:

```bash
claude mcp add --transport stdio opik-mcp \
  --env OPIK_API_KEY=<your-key> \
  --env OPIK_WORKSPACE=<your-workspace> \
  -- uvx opik-mcp
```

Or edit `~/.claude.json` directly:

```json
{
  "mcpServers": {
    "opik-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["opik-mcp"],
      "env": {
        "OPIK_API_KEY": "<your-key>",
        "OPIK_WORKSPACE": "<your-workspace>"
      }
    }
  }
}
```

Restart Claude Code. Verify with `/mcp` — `opik-mcp` should appear as connected.
Then, in the chat, ask: **"list my Opik projects"** — Claude will call the `list`
tool and you'll see your workspace's projects.

### Cursor

Edit `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project), or open
**Cmd+Shift+J → Features → Model Context Protocol**:

```json
{
  "mcpServers": {
    "opik-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["opik-mcp"],
      "env": {
        "OPIK_API_KEY": "<your-key>",
        "OPIK_WORKSPACE": "<your-workspace>"
      }
    }
  }
}
```

Reload Cursor; the green dot next to `opik-mcp` in the MCP panel confirms the
connection. Ask in chat: **"list my Opik projects"**.

> **Cursor 60s timeout.** Cursor enforces a hard tool-call timeout that doesn't
> reset on progress notifications. See [Known host limits](#known-host-limits).

### VS Code Copilot

`.vscode/mcp.json` in your workspace (or User Settings JSON):

```json
{
  "servers": {
    "opik-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["opik-mcp"],
      "env": {
        "OPIK_API_KEY": "<your-key>",
        "OPIK_WORKSPACE": "<your-workspace>"
      }
    }
  }
}
```

Reload the window; the Copilot Chat **MCP** indicator shows `opik-mcp` once
the server is reachable. Ask in chat: **"list my Opik projects"**.

### MCP Inspector (manual testing)

```bash
OPIK_API_KEY=<your-key> OPIK_WORKSPACE=<your-workspace> \
  npx @modelcontextprotocol/inspector uvx opik-mcp
```

### Self-hosted Opik

Add `COMET_URL_OVERRIDE` (and `OPIK_URL` if Opik lives at a non-default path) to
the same `env` block in your host config:

```json
{
  "mcpServers": {
    "opik-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["opik-mcp"],
      "env": {
        "OPIK_API_KEY": "<your-key>",
        "OPIK_WORKSPACE": "<your-workspace>",
        "COMET_URL_OVERRIDE": "https://opik.your-company.com",
        "OPIK_MCP_ANALYTICS_SOURCE": ""
      }
    }
  }
}
```

Omit `OPIK_WORKSPACE` on an open-source deployment, where `default` is the only
workspace; keep it on a self-hosted Comet, which has real named ones.

Setting `OPIK_MCP_ANALYTICS_SOURCE=""` opts your install out of the
cloud-Comet source label on telemetry events.

---

## Tools

`opik-mcp` exposes a small, outcome-oriented surface that covers the full
lifecycle (read → annotate → curate → author → iterate).

| Tool | Purpose |
|---|---|
| [`read`](#read) | Universal read by id / name / `opik://` URI |
| [`list`](#list) | Universal list with optional name filter + pagination |
| [`write`](#write) | Universal write — log traces/spans, score, comment, save prompts, manage test suites & experiments |
| [`schema`](#schema) | Introspect write-operation schemas (used by the LLM to construct valid payloads) |
| `read_skill` | Read one of the Opik agent skills bundled with this server |

### `read`

One tool for any "show me X" question. Takes an `entity_type` plus an `id`
(UUID or, for nameable types, a name) or a full `opik://` URI. Composite reads
(`trace`, `prompt`) inline their children so a single call returns the full
picture.

**Supported entities:** `project`, `trace`, `span`, `test_suite`, `experiment`,
`prompt`. Name-based lookup is available for `project`, `experiment`, `prompt`,
`test_suite` (slower — two API calls — and may return multiple matches).

```python
read(entity_type="trace", id="7f2e3c8a-…")
read(entity_type="project", id="demo")          # name lookup
read(entity_type="trace", id="opik://traces/7f2e3c8a-…")
```

### `list`

Browse a collection with optional name filter and pagination. Project-scoped
types (`trace`, `test_suite_item`, `prompt_version`) require their parent UUID.

```python
list(entity_type="experiment", page=1, size=25)
list(entity_type="experiment", name="rerank")          # name substring filter
list(entity_type="trace", project_id="<project-uuid>") # traces of one project
```

### `write`

Universal write dispatcher. Pass `operation` + `data` and the dispatcher
validates the payload, applies the right REST verb, and returns the
backend response.

**Operations:**

| Operation | What it does |
|---|---|
| `trace.create` | Log a single trace (or a batch). Parent for spans / scores / comments. |
| `trace.update` | Finalize or amend an existing trace. |
| `span.create` | Log a span on an existing trace (or a batch). |
| `score.create` | Attach a numeric feedback score to a trace, span, or thread. |
| `comment.create` | Attach a free-text comment to a trace, span, or thread. |
| `prompt_version.save` | Save a new prompt version (creates the prompt by name if missing). |
| `test_suite.create` | Create an evaluation test suite. |
| `test_suite_item.upsert` | Upsert items into a test suite (always the envelope shape). |
| `experiment.create` | Create an experiment scoped to a test suite. |
| `experiment_item.create` | Attach trace + dataset_item rows to an experiment. |

```python
write(operation="score.create", data={
  "target": "trace",
  "target_id": "7f2e3c8a-…",
  "name": "helpfulness",
  "value": 0.9,
  "reason": "great recovery"
})
```

### `schema`

Inspect the exact JSON shape and required fields of any write operation before
you call it — useful when you're not sure what `data` should look like. Returns
the schema, OAuth scope, and one validated example. Pure lookup, no backend
call.

```python
schema(operation="score.create")
schema(operation="prompt_version.save")
```

---

## Configuration

Every setting is an environment variable. Required ones in **bold**.

### Identity / endpoint

| Variable | Default | Notes |
|---|---|---|
| **`OPIK_API_KEY`** | — | Required for any authenticated read/write. |
| `OPIK_WORKSPACE` | _unset_ | Workspace name. On cloud with an API key, unset sends `default`, which resolves to your account's **default** workspace — set it explicitly if you work in a different one, or reads come from the wrong workspace silently. Leave unset over OAuth (the token carries it) and on local/OSS (`default` is the only workspace there). |
| `COMET_WORKSPACE` | — | Deprecated alias for `OPIK_WORKSPACE` (backward compat). `OPIK_WORKSPACE` wins if both are set. |
| `COMET_WORKSPACE_ID` | _unset_ | Optional workspace UUID. Stamped into analytics events when set, and takes precedence over the resolved one. Rarely needed — OAuth installs get the UUID from the token automatically. |
| `COMET_URL_OVERRIDE` | `https://www.comet.com` | Set to your self-hosted Comet host, or `https://dev.comet.com` for staging. |
| `OPIK_URL` | derived from `COMET_URL_OVERRIDE` + `/opik/api` | Override only if Opik lives on a different host/path than the Comet UI. |
| `OPIK_DEFAULT_PROJECT_NAME` | _unset_ | When set, the per-session `instructions` blob tells the LLM to pass this as `project_name` on every tool call unless the user names a different project. |

### Server / transport

| Variable | Default | Notes |
|---|---|---|
| `OPIK_MCP_TRANSPORT` | `stdio` | `stdio` for host-launched, `streamable-http` to listen on a port. |
| `OPIK_MCP_HOST` | `127.0.0.1` | uvicorn bind host (`streamable-http` only). |
| `OPIK_MCP_PORT` | `8080` | uvicorn bind port (`streamable-http` only). |
| `OPIK_MCP_RELOAD` | `false` | `true` to enable uvicorn `--reload` (dev only). |
| `OPIK_MCP_AS_URL` | _unset_ | OAuth Authorization Server URL, advertised in `/.well-known/oauth-protected-resource` (RFC 9728) and used as the proxy target for AS-discovery probes. Required for MCP hosts to bootstrap the OAuth dance over HTTP. |
| `OPIK_MCP_RESOURCE_URI` | _unset_ | Canonical public URI of this server, advertised as `resource` in the protected-resource metadata and used to derive the `WWW-Authenticate` hint. |
| `OPIK_MCP_OAUTH_VALIDATION_CACHE_TTL_S` | `30` | How long a "valid" answer from opik-backend's token introspection is trusted before the next request on the same OAuth token asks again. Bounds the backend load added by per-request validation and the window in which an expired token is still forwarded (that window also ends on the first 401 the backend returns). Capped by the token's own `expires_at` when the backend reports one. |
| `OPIK_MCP_LOG_LEVEL` | `INFO` | stderr logger threshold. |

#### Choosing a transport

Two bearer shapes, two contracts on HTTP transport. An `opik_mcp_at_…` OAuth
access token is **validated on every request** against opik-backend's token
introspection endpoint (cached, see `OPIK_MCP_OAUTH_VALIDATION_CACHE_TTL_S`);
an expired or revoked token gets an HTTP 401 with
`WWW-Authenticate: Bearer error="invalid_token"`, which is what MCP hosts key
their silent `refresh_token` grant on. An Opik API key is **not validated
locally**: it is forwarded verbatim to opik-backend, which is its single point
of enforcement. Pick the transport by deployment shape:

| Scenario | Transport |
|---|---|
| MCP client and Opik on the same machine (local OSS install) | **stdio** (recommended — simplest, no port, no OAuth setup) |
| Local MCP client → remote Opik (Comet cloud / self-hosted) | stdio with `OPIK_API_KEY`, or HTTP with OAuth (`OPIK_MCP_AS_URL` pointing at the backend) |
| Hosted opik-mcp behind the same edge as opik-backend | **HTTP** — bearers are validated by the backend per request |

Note for local OSS installs: the OSS backend does not authenticate requests,
so an HTTP opik-mcp in front of it is as open as the OSS REST API itself.
Keep the default `127.0.0.1` bind (and prefer stdio) on shared networks.

### Telemetry

Anonymous usage events (event type + timing only — no query content). A SHA-256
digest of your API key is included so support can find your account; the raw
key never leaves the process. **Opt out:** `OPIK_MCP_ANALYTICS_ENABLED=false`.

| Variable | Default | Notes |
|---|---|---|
| `OPIK_MCP_ANALYTICS_ENABLED` | `true` | Set to `false` to disable all telemetry. |
| `OPIK_MCP_ANALYTICS_URL` | `https://stats.comet.com/notify/event/` | Override for staging. |
| `OPIK_MCP_ANALYTICS_ENVIRONMENT` | `prod` | Tag on every event (`prod` / `staging` / `dev`). |
| `OPIK_MCP_ANALYTICS_SOURCE` | `comet.com` | Receiver uses this to mark `on_prem=False`. On-prem installs should override to `""` or their own domain. |
| `OPIK_MCP_ANALYTICS_CONNECT_TIMEOUT_S` | `5.0` | HTTP connect timeout. |
| `OPIK_MCP_ANALYTICS_TOTAL_TIMEOUT_S` | `10.0` | HTTP total request timeout. |

---

## Known host limits

Hosts differ in how long they let a single tool call run:

- **Claude Code** — no documented tool-call timeout. Recommended.
- **Cursor** — hard 60s timeout that does **not** reset on progress
  ([upstream bug](https://forum.cursor.com/t/mcp-tool-timeout/74465)).
- **MCP Inspector** — `MAX_TOTAL_TIMEOUT` bounds total duration (default 60s).
  Raise it in the Inspector UI for long operations.

If a call gets stuck, set `OPIK_MCP_LOG_LEVEL=DEBUG` for the full request log.

---

## Troubleshooting

**`OPIK_API_KEY` isn't picked up** — the var isn't reaching the server
process. In Claude Code / Cursor / VS Code, env vars only apply when inside
the `env` block of the MCP server config, not your shell. Restart the host
after editing.

**Cursor call times out at 60s** — Cursor's known bug, not `opik-mcp`. Either
narrow the call (smaller `size`, a tighter window), or run the same operation
on Claude Code which has no hard cap.

---

## Development

```bash
git clone git@github.com:comet-ml/opik-mcp.git
cd opik-mcp
make install        # uv sync --extra dev
make check          # lint + typecheck + test
make run-dev        # uvicorn with --reload + DEBUG logs
make inspect        # MCP Inspector against the running server
```

Common targets:

| Target | What it does |
|---|---|
| `make install` | `uv sync --extra dev` |
| `make run` | Run the MCP server (stdio by default). |
| `make run-dev` | Run with DEBUG logging + uvicorn `--reload`. |
| `make dev` | Run via `mcp dev` (Inspector dev-mode wrapper). |
| `make inspect` | Launch MCP Inspector against a running server. |
| `make test` | `uv run pytest -q`. |
| `make lint` | `ruff check` + format check. |
| `make format` | `ruff format` + `ruff check --fix`. |
| `make typecheck` | `mypy`. |
| `make check` | `lint + typecheck + test`. |

Repo layout:

```
opik-mcp/
├── src/opik_mcp/        ← server, tools, analytics
├── tests/               ← pytest suites
├── scripts/             ← live-BE smoke + MCP-session smoke
├── legacy/typescript/   ← deprecated v2 TS server
├── pyproject.toml
└── Makefile
```

---

## Get help

- [Open an issue](https://github.com/comet-ml/opik-mcp/issues) for bugs and feature requests
- [Opik docs](https://www.comet.com/docs/opik/) for SDK / backend documentation
- [Comet community Slack](https://chat.comet.com/) for questions

---

> **Upgrading from v2?** The legacy TypeScript server still ships on npm as
> `opik-mcp@^2` (`npx -y opik-mcp`); source is preserved under
> [`legacy/typescript/`](./legacy/typescript/). See
> [`legacy/typescript/DEPRECATED.md`](./legacy/typescript/DEPRECATED.md) for
> the support policy.

---

## License

Apache-2.0.
