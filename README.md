# opik-mcp

> **Migrating from the old `npx opik-mcp`?** The TypeScript server is deprecated
> and sunsets on **2026-11-15**. Swap `npx -y opik-mcp` for **`uvx opik-mcp@latest`**
> in your MCP client config. Full guide: [`legacy/typescript/MIGRATION.md`](./legacy/typescript/MIGRATION.md).

**Model Context Protocol server for [Opik](https://www.comet.com/opik) + Ollie.**
Plug your AI host (Claude Code, Cursor, VS Code Copilot, MCP Inspector) directly
into your Opik workspace — read traces, log scores, save prompt versions, and
ask Ollie investigative questions, all from the chat.

Built for LLM engineers who already run Opik and want to drive it from the same
AI assistant they code with.

```
You:    "Why did the experiment 'gpt-4o-rerank-v3' regress on factuality?"
Claude: → ask_ollie → reads experiment + traces → "Three traces failed because…"

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
- **`OPIK_WORKSPACE`** — your workspace name (lowercase, as it appears in the URL). E.g. `https://www.comet.com/acme-ai/...` → `OPIK_WORKSPACE=acme-ai`. Optional — defaults to `default` (the Opik SDK convention), which is correct for local/OSS installs; cloud users with a named workspace should set it. `COMET_WORKSPACE` is accepted as a deprecated alias.

> **Pre-release note:** `opik-mcp` (Python) is not yet published to PyPI. Until
> the first PyPI release lands, replace `uvx opik-mcp` in any snippet below with:
> `uvx --from git+https://github.com/comet-ml/opik-mcp.git opik-mcp`

> **`OPIK_WORKSPACE` is optional.** Omit the `OPIK_WORKSPACE` line/key in any
> snippet below and the server uses the `default` workspace (correct for
> local/OSS installs). Set it only if you connect to a named cloud workspace.

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
> reset on progress notifications. Long `ask_ollie` turns will fail on Cursor.
> See [Known host limits](#known-host-limits).

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
        "COMET_URL_OVERRIDE": "https://opik.your-company.com",
        "OPIK_MCP_ANALYTICS_SOURCE": ""
      }
    }
  }
}
```

`ask_ollie` and `run_experiment` are available on Comet Cloud only — on
self-hosted those calls will fail at dispatch, so use `read` / `list` / `write`
directly. Setting `OPIK_MCP_ANALYTICS_SOURCE=""` opts your install out of the
cloud-Comet source label on telemetry events.

---

## Tools

`opik-mcp` exposes a small, outcome-oriented surface — six tools that cover
the full lifecycle (read → annotate → curate → author → iterate).

| Tool | Purpose |
|---|---|
| [`read`](#read) | Universal read by id / name / `opik://` URI |
| [`list`](#list) | Universal list with optional name filter + pagination |
| [`ask_ollie`](#ask_ollie) | Investigate / synthesize via the Opik in-product assistant |
| [`write`](#write) | Universal write — log traces/spans, score, comment, save prompts, manage test suites & experiments |
| [`schema`](#schema) | Introspect write-operation schemas (used by the LLM to construct valid payloads) |
| [`run_experiment`](#run_experiment) | Run an evaluation experiment end-to-end via Ollie |

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

### `ask_ollie`

For investigative questions, cross-entity synthesis, or anything that needs
Opik domain expertise. Ollie has direct read access to your workspace and can
execute writes (scores, comments, test-suite items, prompt versions) mid-stream
when asked.

```python
ask_ollie(query="Why are spans in project 'demo' slower this week than last?")
ask_ollie(query="Compare experiments A and B on factuality. Score the bottom 5 traces of A 0.2 with reason.")
```

Returns the assistant's final text plus a `thread_id`. Pass it back on
follow-ups to preserve context — Ollie has no memory across threads.

**YOLO mode (default).** Writes Ollie performs mid-stream execute without a
per-action confirmation. Each auto-approval is logged as a JSON audit row on
the `opik_mcp.audit` Python logger. To require confirmation instead, set
`OPIK_MCP_AUTO_APPROVE=disabled` — Ollie's confirm requests then surface as
typed errors you can manually re-issue.

> Available on Comet Cloud only.

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

### `run_experiment`

Run an evaluation experiment end-to-end via Ollie. Takes a single
`experiment_config` dict that mirrors Opik's experiment shape (prompt, test
suite, scorers); Ollie executes the run and writes results back as an Opik
experiment.

```python
run_experiment(experiment_config={
  "test_suite_name": "qa-eval-v2",
  "prompt_name": "welcome-msg",
  # … see `schema(operation="experiment.create")` for the full shape
})
```

> Available on Comet Cloud only.

---

## Configuration

Every setting is an environment variable. Required ones in **bold**.

### Identity / endpoint

| Variable | Default | Notes |
|---|---|---|
| **`OPIK_API_KEY`** | — | Required for `ask_ollie` and any authenticated read/write. |
| `OPIK_WORKSPACE` | `default` | Workspace name. Optional — falls back to `default` (Opik SDK convention). Cloud users with a named workspace should set it. |
| `COMET_WORKSPACE` | — | Deprecated alias for `OPIK_WORKSPACE` (backward compat). `OPIK_WORKSPACE` wins if both are set. |
| `COMET_WORKSPACE_ID` | — | Optional workspace UUID. Stamped into analytics events when set so BI can join on a stable id rather than the (mutable) workspace name. |
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
| `OPIK_MCP_DEV_TOKEN` | `dev-token-123` | Bearer token the HTTP transport requires. |
| `OPIK_MCP_LOG_LEVEL` | `INFO` | stderr logger threshold. |

### Ollie / long calls

| Variable | Default | Notes |
|---|---|---|
| `OPIK_MCP_AUTO_APPROVE` | `enabled` | `disabled` to require a per-action approval before Ollie's mid-stream writes proceed. On hosts that advertise the MCP `elicitation` capability the user sees a yes/no prompt; on dumber hosts the request surfaces as a typed error you can manually re-issue. |
| `OPIK_MCP_ELICIT_TIMEOUT_SECONDS` | `60` | How long Ollie's mid-stream confirmation prompt may wait for the user before being treated as a cancel. `0` disables the bound (debug only). |
| `OPIK_MCP_POD_READY_TIMEOUT_S` | `120` | Ollie pod cold-start poll cap. |
| `OPIK_MCP_POD_READY_INTERVAL_S` | `2` | Cold-start poll interval. |
| `OPIK_MCP_HEARTBEAT_INTERVAL_S` | `15.0` | Watchdog cadence — emits a `notifications/progress` tick when the pod is silent, keeping host timeouts at bay. |
| `OPIK_MCP_STREAM_IDLE_TIMEOUT_S` | `300.0` | Hard ceiling on pod silence before `ask_ollie` aborts. `0` disables (debug only). |

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

The MCP spec lets hosts reset their tool-call timeout on
`notifications/progress` — `opik-mcp` emits one per Ollie SSE event plus a
15-second watchdog heartbeat. Reality is uneven:

- **Claude Code** — no documented tool-call timeout; heartbeat keeps the call
  alive until `message_end`. Recommended.
- **Cursor** — hard 60s timeout that does **not** reset on progress
  ([upstream bug](https://forum.cursor.com/t/mcp-tool-timeout/74465)).
  Long Ollie turns will fail. Keep `ask_ollie` queries focused.
- **MCP Inspector** — `MAX_TOTAL_TIMEOUT` bounds total duration (default 60s).
  Raise it in the Inspector UI for long operations.

If a call gets stuck, set `OPIK_MCP_LOG_LEVEL=DEBUG` — heartbeat failures
(usually host disconnects) are logged on `opik_mcp.ask_ollie` at debug level.

---

## Troubleshooting

**`OPIK_API_KEY is required to use ask_ollie`** — the var isn't reaching the
server process. In Claude Code / Cursor / VS Code, env vars only apply when
inside the `env` block of the MCP server config, not your shell. Restart the
host after editing.

**`ask_ollie` returns "pod not ready" after 2 minutes** — the Ollie pod
cold-start exceeded `OPIK_MCP_POD_READY_TIMEOUT_S`. Retry — the second call
usually hits a warm pod.

**`ask_ollie` / `run_experiment` fails with a dispatch error on self-hosted
Opik** — those tools are available on Comet Cloud only. Use `read` / `list` /
`write` directly on self-hosted.

**Cursor call times out at 60s** — Cursor's known bug, not `opik-mcp`. Either
shorten the Ollie query, or run the same operation on Claude Code which has no
hard cap.

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
| `make test-live` | Live end-to-end against `dev.comet.com` (set `OPIK_API_KEY` + `OPIK_WORKSPACE`). |
| `make lint` | `ruff check` + format check. |
| `make format` | `ruff format` + `ruff check --fix`. |
| `make typecheck` | `mypy`. |
| `make check` | `lint + typecheck + test`. |

Repo layout:

```
opik-mcp/
├── src/opik_mcp/        ← server, tools, ask_ollie, analytics
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
