# Opik MCP Server

**The official Model Context Protocol (MCP) server for [Opik](https://github.com/comet-ml/opik), the open-source LLM observability and evaluation platform, built by [Comet](https://www.comet.com).**
Plug your AI host (Claude Code, Cursor, VS Code Copilot, MCP Inspector) directly
into your Opik workspace: read traces, log scores, save prompt versions, and ask
[Ollie](#ask_ollie), Opik's in-product AI assistant, investigative questions, all
from the chat.

Built for LLM engineers who already run Opik and want to drive it from the same
AI assistant they code with.

> **Migrating from the old `npx opik-mcp`?** The TypeScript server is deprecated
> and sunsets on **2026-11-15**. Swap `npx -y opik-mcp` for **`uvx opik-mcp@latest`**
> in your MCP client config. Full guide: [`legacy/typescript/MIGRATION.md`](./legacy/typescript/MIGRATION.md).

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

`opik-mcp` exposes a small, outcome-oriented surface — seven tools that cover
the full lifecycle (read → review → annotate → curate → author → iterate).

| Tool | Purpose |
|---|---|
| [`read`](#read) | Universal read by id / name / `opik://` URI |
| [`list`](#list) | Universal list with optional name filter + pagination |
| [`ask_ollie`](#ask_ollie) | Investigate / synthesize via the Opik in-product assistant |
| [`write`](#write) | Universal write — log traces/spans, score, comment, save prompts, manage test suites & experiments |
| [`schema`](#schema) | Introspect write-operation schemas (used by the LLM to construct valid payloads) |
| [`review`](#interactive-review-mcp-apps) | Open a thread or annotation queue for human review, with an interactive panel where the host supports it |
| [`run_experiment`](#run_experiment) | Run an evaluation experiment end-to-end via Ollie |

### `read`

One tool for any "show me X" question. Takes an `entity_type` plus an `id`
(UUID or, for nameable types, a name) or a full `opik://` URI. Composite reads
(`trace`, `prompt`) inline their children so a single call returns the full
picture.

**Supported entities:** `project`, `trace`, `span`, `thread`, `test_suite`,
`experiment`, `prompt`, `annotation_queue`. Name-based lookup is available for
`project`, `experiment`, `prompt`, `test_suite`, `annotation_queue` (slower — two
API calls — and may return multiple matches).

`read` is always pure data. To put something in front of a human, use
[`review`](#interactive-review-mcp-apps) instead.

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
| `thread.close` / `thread.open` | Mark a conversation thread done / reopen it. |
| `annotation_queue.create` | Put traces/threads aside for human review, with reviewer instructions and a rubric. |
| `annotation_queue_item.add` | Add items to a queue — pass `thread_ids` (strings) and the project, or resolved `ids`. |
| `feedback_definition.create` | Define a scoring rubric (categorical / numerical / boolean) queues and rules can reference by name. |

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

## Interactive review (MCP Apps)

`review(entity_type, id)` returns the same text `read` would, and on hosts that
implement the [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview)
extension (`io.modelcontextprotocol/ui`) — Claude Desktop, Claude on the web, VS Code
Copilot, Goose and others — it also renders an interactive panel:

- **`review('thread', …)`** — the conversation turn by turn with latency, cost and tokens
  per turn, a per-turn latency/cost sparkline above the transcript (the slowest turn
  is highlighted, failed turns are red, a click jumps to the turn), thumbs up/down per
  answer, a thread-level score, a comment box, and *Close thread*. Scores an online evaluation rule already wrote show as dashed
  pills, so the person sees what the judge said before deciding.
- **`review('annotation_queue', …)`** — the reviewer's instructions, progress across the
  queue, item navigation, and score controls generated from the queue's own feedback
  definitions, with the rule's verdict beside each control (it turns red when the
  human overrules it). *Skip* bookmarks a thread without writing anything; the
  keyboard does the rest (`J`/`K` threads, `1`–`9` options, `S` skip, `⌘/Ctrl+Enter`
  save). Each save pushes the verdict to the model silently via
  `ui/update-model-context` — scores, skipped threads and human-vs-rule disagreements;
  *Finish review* adds one short message so the agent takes a turn, and the panel
  switches to a completed state. A human decision continues the agent's work without
  anyone retyping it.

The panel follows the host: it applies the host's style tokens and fonts from
`hostContext.styles` (Opik's palette is the fallback), tracks theme changes, sizes
itself to `containerDimensions` and `safeAreaInsets`, and offers a full-screen toggle
via `ui/request-display-mode` for long conversations. It answers
`ui/resource-teardown`, previews streamed `tool-input-partial` arguments, and hides
controls the host did not advertise in `hostCapabilities`.

Every action in the panel goes through the same [`write`](#write) operations the model
uses, so scores and comments are attributed to the human and audited identically.

`review` is the only tool the panel is attached to (via `_meta.ui.resourceUri`), which
keeps `read` a pure data call — attaching it to `read` would have opened a UI for every
entity, including the ones with no purpose-built view. The app's data channel is marked
`visibility: ["app"]`, so it stays out of the model's tool list entirely. Hosts that
don't negotiate the extension get exactly the text they got before.

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
| `OPIK_MCP_AS_URL` | _unset_ | OAuth Authorization Server URL, advertised in `/.well-known/oauth-protected-resource` (RFC 9728) and used as the proxy target for AS-discovery probes. Required for MCP hosts to bootstrap the OAuth dance over HTTP. |
| `OPIK_MCP_RESOURCE_URI` | _unset_ | Canonical public URI of this server, advertised as `resource` in the protected-resource metadata and used to derive the `WWW-Authenticate` hint. |
| `OPIK_MCP_LOG_LEVEL` | `INFO` | stderr logger threshold. |

#### Choosing a transport

opik-mcp performs **no local credential validation** on HTTP transport: any
well-formed `Authorization: Bearer …` (an Opik API key or an `opik_mcp_at_…`
OAuth access token) is forwarded verbatim to opik-backend, which is the
single point of auth enforcement. Pick the transport by deployment shape:

| Scenario | Transport |
|---|---|
| MCP client and Opik on the same machine (local OSS install) | **stdio** (recommended — simplest, no port, no OAuth setup) |
| Local MCP client → remote Opik (Comet cloud / self-hosted) | stdio with `OPIK_API_KEY`, or HTTP with OAuth (`OPIK_MCP_AS_URL` pointing at the backend) |
| Hosted opik-mcp behind the same edge as opik-backend | **HTTP** — bearers are validated by the backend per request |

Note for local OSS installs: the OSS backend does not authenticate requests,
so an HTTP opik-mcp in front of it is as open as the OSS REST API itself.
Keep the default `127.0.0.1` bind (and prefer stdio) on shared networks.

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
make install        # uv sync --extra dev (also writes the git-ignored _version.py)
make check          # lint + typecheck + test — the gate to run before any push
make run-dev        # uvicorn with --reload + DEBUG logs
make inspect        # MCP Inspector against the running server
```

`make install` needs [`uv`](https://docs.astral.sh/uv/) and Python 3.13+; `uv`
provisions the interpreter itself, so nothing else is required on the machine.
`make check` needs no API key and no network: the suite stubs the Opik backend and
`tests/conftest.py` disables analytics. A green run is a clean `ruff` + `mypy`
and `1177 passed, 2 skipped` (the count moves as tests land).

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
| `make conformance` | `pytest tests/conformance -v` — the MCP wire contract (tool inventory, schemas, `ui://` resource). Run this after any change to the tool surface. |
| `make check` | `lint + typecheck + test`. |

Repo layout:

```
opik-mcp/
├── src/opik_mcp/        ← server, tools, ask_ollie, analytics
│   ├── read_list/       ← read + list registry (one entry per entity type)
│   ├── writes/          ← write dispatch, models, operation registry
│   └── apps/            ← MCP App: the review panel (HTML + payload builder)
├── tests/               ← pytest suites (tests/conformance = MCP wire contract)
├── scripts/             ← live-BE smoke + MCP-session smoke
├── legacy/typescript/   ← deprecated v2 TS server
├── pyproject.toml
└── Makefile
```

### Running your local checkout in a host

Point any MCP client at the working tree instead of the published package — same
config as [Install](#install), with `uvx opik-mcp` swapped for
`uv run --directory <abs-path-to-checkout> opik-mcp`. Claude Code:

```bash
claude mcp add --transport stdio opik-mcp-local \
  --env OPIK_API_KEY=<your-key> \
  --env OPIK_WORKSPACE=<your-workspace> \
  -- uv run --directory /abs/path/to/opik-mcp opik-mcp
```

Or in `~/.claude.json` / `.cursor/mcp.json` / `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "opik-mcp-local": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/opik-mcp", "opik-mcp"],
      "env": {
        "OPIK_API_KEY": "<your-key>",
        "OPIK_WORKSPACE": "<your-workspace>",
        "OPIK_MCP_LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

Use a distinct server name (`opik-mcp-local`) so it can coexist with an
installed `opik-mcp`. The host spawns a fresh process per session, so a code
edit takes effect on the next host restart — no reinstall step. Against a dev
or self-hosted deployment, add `COMET_URL_OVERRIDE` (see
[Self-hosted Opik](#self-hosted-opik)).

The Inspector is the fastest way to look at the raw wire without a host:

```bash
OPIK_API_KEY=<your-key> OPIK_WORKSPACE=<your-workspace> \
  npx @modelcontextprotocol/inspector uv run --directory $(pwd) opik-mcp
```

### Verifying a change

| What you changed | What to run |
|---|---|
| Anything | `make check` |
| The tool surface (new tool, renamed arg, new write op) | `make conformance` — snapshot tests in `tests/conformance/snapshots/` pin the advertised JSON Schemas. Review the diff, then update the snapshot deliberately. |
| The review panel | `uv run pytest tests/test_apps tests/conformance/test_tool_inventory.py -v` |
| Write dispatch | `uv run python scripts/smoke_mcp_session.py` — drives a real MCP session and dry-runs every write op, printing the request the backend *would* receive — nothing is written. |
| Anything touching live behaviour | `make test-live` (needs `OPIK_API_KEY` + `OPIK_WORKSPACE` against `dev.comet.com`) |

The wire-contract suite is the one that catches accidental surface drift: it
asserts the exact set of model-facing tools, that `app_data` stays app-only, and
that the `ui://` resource is advertised with the MCP App mime type.

### Trying the review panel

`review` needs a host that negotiates the [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview)
extension to show the panel (Claude Desktop / Claude on the web, VS Code Copilot,
Goose). In a host without it — Claude Code today, MCP Inspector — the tool still
returns its text, which is the point of the design: nothing depends on the panel
appearing. One caveat for such hosts: they do not know `_meta.ui.visibility`, so
the app-only `app_data` tool appears in the model's list there; its description
tells the model to use `read()` instead.

To exercise it you need something reviewable in your workspace:

```python
# a conversation to review — any project with multi-turn threads
list(entity_type="thread", project_id="<project-uuid>")
review(entity_type="thread", id="<thread-id>", project_id="<project-uuid>")

# or a queue: create one, fill it, then open it
write(operation="feedback_definition.create", data={...})   # schema("feedback_definition.create")
write(operation="annotation_queue.create", data={...})
write(operation="annotation_queue_item.add", data={...})
review(entity_type="annotation_queue", id="<queue-name-or-uuid>")
```

`schema(operation=…)` returns the JSON Schema plus a worked example for each of
those operations. Scores and comments saved in the panel go through the same
`write` operations the model uses, so they show up in the Opik UI and in
`read(entity_type="annotation_queue", …)` identically.

The panel itself is `src/opik_mcp/apps/review_html.py` — a single self-contained
HTML document (inlined CSS/JS, no external fetches, so it needs no CSP
relaxations). `src/opik_mcp/apps/__init__.py` registers the `ui://` resource and
builds the full-fidelity payload the iframe loads via the app-only `app_data`
tool.

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
