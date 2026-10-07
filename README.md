# Opik MCP Server

**The official Model Context Protocol (MCP) server for [Opik](https://github.com/comet-ml/opik), the open-source LLM observability and evaluation platform, built by [Comet](https://www.comet.com).**
Plug your AI host (Claude Code, Cursor, VS Code Copilot, Codex, opencode, or any
MCP client) directly into your Opik workspace: read traces, log scores, and save
prompt versions, all from the chat.

Built for LLM engineers who already run Opik and want to drive it from the same
AI assistant they code with.

```
You:    "Which traces in project 'demo' failed today?"
Claude: → list(entity_type="trace", project_name="demo") → "Three traces failed…"

You:    "Score trace 7f2e… 0.9 on helpfulness with reason 'great recovery'."
Claude: → write(score.create) → done
```

---

## Quick start

One command registers the server with the AI clients on your machine, installs
the Opik skill pack, and verifies the connection. It needs [`uv`](https://docs.astral.sh/uv/)
and no Opik SDK:

```bash
uvx opik mcp configure
```

It detects Claude Code, Cursor, VS Code Copilot, Codex and opencode, and sets up
the server that fits your Opik:

| Your Opik | Server | Transport | Sign-in |
|---|---|---|---|
| Opik Cloud (`www.comet.com`) | [the hosted server](#opik-cloud-the-hosted-server), run by Comet | Streamable HTTP | in the browser (OAuth); no API key |
| Self-hosted Comet, open-source Opik | [the local server](#self-hosted-and-open-source-opik-the-local-server), this package | stdio | env vars; an API key only where the deployment needs one |

Clients load MCP servers when a session starts, so start a new session
afterwards. Without a terminal, as from a coding agent or a script, name the
client: `uvx opik mcp configure --ai-client claude-code` (or `codex`, `cursor`,
`vscode`, `opencode`). Run that way it needs an existing `~/.opik.config` or
`OPIK_API_KEY` in the environment; without either, use the commands below.

Setup guide, troubleshooting and FAQ: [comet.com/docs/opik/mcp-server](https://www.comet.com/docs/opik/mcp-server).

---

## Opik Cloud: the hosted server

Comet runs the server at `https://www.comet.com/opik/api/v1/mcp`. Your client
connects over HTTP and opens a browser sign-in the first time. There is nothing
to install, no API key, and no workspace to set: the server works in the
workspace you pick when you sign in. After adding it, start a new session and
ask: **"list my Opik projects"**.

[![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=opik-mcp&config=eyJ1cmwiOiJodHRwczovL3d3dy5jb21ldC5jb20vb3Bpay9hcGkvdjEvbWNwIn0%3D)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Server-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=opik-mcp&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fwww.comet.com%2Fopik%2Fapi%2Fv1%2Fmcp%22%7D)

### Claude Code

```bash
claude mcp add --scope user --transport http opik-mcp https://www.comet.com/opik/api/v1/mcp
claude mcp login opik-mcp
```

`--scope user` makes the server available in every project; without it, Claude
Code registers it for the current directory only. `claude mcp login` opens the
sign-in; `/mcp` → **Authenticate** in a session does the same. Over SSH,
`claude mcp login opik-mcp --no-browser` prints the sign-in URL to open on your
own machine; the last step needs an interactive terminal (`ssh -t`).

### Codex

```bash
codex mcp add opik-mcp --url https://www.comet.com/opik/api/v1/mcp
```

`add` opens the sign-in; `codex mcp login opik-mcp` opens it again. In
`~/.codex/config.toml` the same server is:

```toml
[mcp_servers.opik-mcp]
url = "https://www.comet.com/opik/api/v1/mcp"
```

### Cursor

Use the button above, or add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "opik-mcp": {
      "url": "https://www.comet.com/opik/api/v1/mcp"
    }
  }
}
```

### VS Code Copilot

Use the button above, or add to `.vscode/mcp.json` in your workspace or to your
user `mcp.json` (**MCP: Open User Configuration**):

```json
{
  "servers": {
    "opik-mcp": {
      "type": "http",
      "url": "https://www.comet.com/opik/api/v1/mcp"
    }
  }
}
```

### Other clients

Any client that takes a URL can use the hosted server:

```bash
npx add-mcp https://www.comet.com/opik/api/v1/mcp --name opik-mcp
```

A client that can only start local commands can reach it through
`npx -y mcp-remote https://www.comet.com/opik/api/v1/mcp`.

### Opik Cloud with an API key

Where nobody can complete the browser sign-in, as when an agent runs unattended
from a script or a client has no MCP OAuth support, use an API key from
[`comet.com/api/my/settings/`](https://www.comet.com/api/my/settings/) instead,
with [the local server](#self-hosted-and-open-source-opik-the-local-server)
pointed at Opik Cloud:

```bash
claude mcp add --scope user opik-mcp \
  --env OPIK_API_KEY="$OPIK_API_KEY" \
  --env OPIK_WORKSPACE=<workspace> \
  -- uvx opik-mcp
```

The other clients take the same two variables in their `env` block. Set
`OPIK_WORKSPACE` to the segment after `comet.com/opik/` in your Opik URL
(`https://www.comet.com/opik/acme-ai/projects` → `acme-ai`). Left out, the
server sends `default`, which Comet resolves to your account's default
workspace, so reads can come from the wrong workspace without an error.

---

## Self-hosted and open-source Opik: the local server

`opik-mcp` runs on your machine: the client starts it with `uvx opik-mcp` and
talks to it over stdio. Install [`uv`](https://docs.astral.sh/uv/) once; it
fetches the package, and Python 3.13 if needed, on first use:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# or: brew install uv
```

`uvx opik-mcp` reuses the copy uv has cached, so it starts quickly;
`uv cache clean opik-mcp` makes the next start fetch the newest release.
`uvx opik-mcp@latest` asks PyPI on every start, which adds about a second and a
half. Codex waits `mcp_optional_startup_grace_ms` (1 s by default) for servers
before it builds the first tool list, so a slower start can leave the tools out
of the first turn.

Env vars point the server at your Opik:

| Your Opik | Env vars |
|---|---|
| Open-source Opik on this machine | `OPIK_URL=http://localhost:5173/api` |
| Open-source Opik on a server | `OPIK_URL=https://<host>/api`, plus `OPIK_API_KEY` only if the deployment adds authentication |
| Self-hosted Comet platform (the Opik UI is at `https://<host>/opik`) | `COMET_URL_OVERRIDE=https://<host>`, `OPIK_WORKSPACE`, `OPIK_API_KEY` |

- Open source serves its API at `/api` and has one workspace, `default`, so it
  needs no `OPIK_WORKSPACE`. `COMET_URL_OVERRIDE` would point the server at
  `/opik/api`, which open source does not serve.
- A self-hosted Comet has named workspaces. Set `OPIK_WORKSPACE` to the segment
  after `/opik/` in your Opik URL; left out, reads come from your account's
  default workspace, with no error to say so.
- A self-hosted Comet that runs the MCP OAuth server (off by default) can use
  the hosted flow instead, at `https://<host>/opik/api/v1/mcp`.
- Substitute every value. The server refuses a placeholder workspace such as
  `<your-workspace>` or `${input:OPIK_WORKSPACE}`; a placeholder URL is sent
  as-is and fails to connect.
- `OPIK_MCP_ANALYTICS_SOURCE=""` in the `env` block opts a self-hosted install
  out of the cloud-Comet source label on telemetry events.

Before adding the server to a client, check the settings: run it with the same
env and `--check`. It makes one call to Opik and prints the workspace and how
many projects it sees, or what to fix with a link to the docs, and exits 0 or 1.
A client can't tell you this before a new session: Claude Code shows Connected
even with a wrong key, and Codex starts nothing until then.

```bash
OPIK_URL=http://localhost:5173/api uvx opik-mcp --check
```

The examples use a local open-source Opik. For a self-hosted Comet, swap in the
three variables from the table. After adding the server, start a new session
and ask: **"list my Opik projects"**.

### Claude Code

```bash
claude mcp add --scope user opik-mcp --env OPIK_URL=http://localhost:5173/api -- uvx opik-mcp
```

On a self-hosted Comet, with the key in your shell's `OPIK_API_KEY`:

```bash
claude mcp add --scope user opik-mcp \
  --env COMET_URL_OVERRIDE=https://<host> \
  --env OPIK_WORKSPACE=<workspace> \
  --env OPIK_API_KEY="$OPIK_API_KEY" \
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
        "OPIK_URL": "http://localhost:5173/api"
      }
    }
  }
}
```

`claude mcp get opik-mcp` shows `✔ Connected` once the server starts. That does
not prove the URL or key are right, because only a tool call reaches Opik;
`--check`, above, does. It also prints the `env` block, API key included.

### Codex

```bash
codex mcp add opik-mcp --env OPIK_URL=http://localhost:5173/api -- uvx opik-mcp
```

Or edit `~/.codex/config.toml`:

```toml
[mcp_servers.opik-mcp]
command = "uvx"
args = ["opik-mcp"]
env = { OPIK_URL = "http://localhost:5173/api" }
# On a self-hosted Comet, forward the key from the environment Codex starts in,
# so it stays out of this file:
# env_vars = ["OPIK_API_KEY"]
# Time allowed for the server to start (default 10 s); the first start
# downloads the package.
startup_timeout_sec = 30
```

Codex gives a local server only the variables in `env` and the names in
`env_vars`, so a key exported in your shell does not reach it otherwise.

### Cursor

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project), or use
**Cmd+Shift+J → Features → Model Context Protocol**:

```json
{
  "mcpServers": {
    "opik-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["opik-mcp"],
      "env": {
        "OPIK_URL": "http://localhost:5173/api"
      }
    }
  }
}
```

> **Cursor 60s timeout.** Cursor enforces a hard tool-call timeout that doesn't
> reset on progress notifications. See [Known host limits](#known-host-limits).

### VS Code Copilot

Add to `.vscode/mcp.json` in your workspace, or to your user `mcp.json`
(**MCP: Open User Configuration**):

```json
{
  "servers": {
    "opik-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["opik-mcp"],
      "env": {
        "OPIK_URL": "http://localhost:5173/api"
      }
    }
  }
}
```

### MCP Inspector (manual testing)

```bash
OPIK_URL=http://localhost:5173/api npx @modelcontextprotocol/inspector uvx opik-mcp
```

---

## Install with a coding agent

For an AI agent asked to install the server. The commands are in the two
sections above; these rules pick which one to run. `uvx opik-mcp --help`
prints these rules and the commands for each case.

1. If Opik runs on this machine — `curl -s http://localhost:5173/api/is-alive/ping`
   answers — use [the local server](#self-hosted-and-open-source-opik-the-local-server)
   with `OPIK_URL=http://localhost:5173/api`. In a sandboxed shell, such as
   Codex's, a failed check can mean the shell has no network access rather than
   that Opik is down; ask the user.
2. If the user is on Opik Cloud (`www.comet.com`), use
   [the hosted server](#opik-cloud-the-hosted-server).
3. Otherwise ask the user for their Opik URL, and use the local server with the
   env vars for that deployment.

- Don't ask the user to paste an API key into the chat. The hosted server needs
  none; otherwise take it from the shell environment (`"$OPIK_API_KEY"`), or let
  the user run the command.
- Don't guess the workspace. It is the segment after `comet.com/opik/` (or
  `/opik/` on a self-hosted Comet) in the user's Opik URL.
- Before registering the local server, run it with the env you will pass and
  `--check`, with `OPIK_API_KEY` exported in the shell rather than written on
  the command: `OPIK_URL=http://localhost:5173/api uvx opik-mcp --check`. It
  exits 0 and names the workspace it sees, or prints what to fix. Once
  registered, Claude Code reports Connected even with a wrong key, and Codex
  has no check.
- An `opik-mcp` entry may already exist, from the old npx setup or an earlier
  attempt. Tell the user before replacing it. Claude Code refuses to add over
  it, so remove it first with `claude mcp remove opik-mcp --scope user`;
  Codex's `add` replaces it.
- After registering, in Claude Code, read only the status line:
  `claude mcp get opik-mcp | grep Status`. The full output prints the `env`
  block, API key included. The hosted server shows `! Needs authentication`
  until `claude mcp login opik-mcp` has run. Codex has nothing that starts the
  server before a session; `codex mcp get opik-mcp` shows what was stored, with
  env values masked.
- Clients load MCP servers when a session starts. Ask the user to start a new
  session, then try **"list my Opik projects"**.

---

## Coming from `npx opik-mcp`?

The TypeScript server (npm `opik-mcp@2`) is deprecated and stops serving
requests on **2026-11-15**. On Opik Cloud, switch to the hosted server and drop
the API key. Otherwise, in your MCP client config, replace `npx -y opik-mcp`
with `uvx opik-mcp`. Some env vars were renamed and command-line flags are no
longer read: see the [migration
guide](https://github.com/comet-ml/opik-mcp/blob/main/legacy/typescript/MIGRATION.md).
Support policy: [`DEPRECATED.md`](https://github.com/comet-ml/opik-mcp/blob/main/legacy/typescript/DEPRECATED.md).
The TypeScript source is at the git tag `legacy-typescript-final`.

---

## Tools

`opik-mcp` exposes a small, outcome-oriented surface that covers the full
lifecycle (read → annotate → curate → author → iterate).

| Tool | Purpose |
|---|---|
| [`read`](#read) | Universal read by id / name / `opik://` URI |
| [`list`](#list) | Universal list with optional name filter + pagination |
| [`write`](#write) | Universal write — log traces/spans, score, comment, save prompts, manage datasets & experiments |
| [`schema`](#schema) | Introspect write-operation schemas (used by the LLM to construct valid payloads) |
| `read_skill` | Read one of the Opik agent skills bundled with this server |

### `read`

One tool for any "show me X" question. Takes an `entity_type` plus an `id`
(UUID or, for nameable types, a name) or a full `opik://` URI. Composite reads
(`trace`, `prompt`, `thread`, `agent_insights_issue`) inline their children so
a single call returns the full picture.

The record you name comes back whole. Inlined children do not: their bodies
are fetched with the backend's `truncate=true`, so a field over ~10 KB is cut
in ClickHouse and base64 images are replaced with `"[image]"` — one attachment
echoed across 200 spans would otherwise cost more than everything else in the
read. The answer says so in `spanBodies` / `messageBodies`, and any child is
whole again through its own `read("span", id)` or `read("trace", trace_id)`,
which hit endpoints that have no `truncate` parameter at all.

An inlined collection is also bounded in length: 200 spans, 200 turns, 100
prompt versions. Past that, `spansTruncated` / `messagesTruncated` /
`versionsTruncated` is `true` and a `moreSpans` / `moreMessages` /
`moreVersions` line beside it carries the count and the exact `list(...)` call
that continues from where the inlined part stopped.

**Supported entities:** `project`, `trace`, `span`, `dataset`, `dataset_item`,
`experiment`, `prompt`, `thread`, `agent_insights_issue`. Name-based lookup is
available for `project`, `experiment`, `prompt`, `dataset` (slower — two API
calls — and may return multiple matches). `thread` and `agent_insights_issue`
are project-scoped: pass `project_id` or `project_name`, or a link/URI that
carries the project. `dataset` and `dataset_item` were called `test_suite` and
`test_suite_item` before; the old names still resolve, but they are not
advertised and new code should use the new ones.

```python
read(entity_type="trace", id="7f2e3c8a-…")
read(entity_type="project", id="demo")  # name lookup
read(entity_type="trace", id="opik://traces/7f2e3c8a-…")
read(entity_type="agent_insights_issue", id="<issue-uuid>", project_id="<project-uuid>")
read(
    entity_type="agent_insights_issue",
    id="https://www.comet.com/opik/<ws>/projects/<pid>/diagnostics?issue=<id>",
)
```

A link copied from the Opik UI works as the `id`: a thread link or a
Diagnostics page link carries the project, so no `project_id` is needed and
the entity type is taken from the link.

A `project` read answers "how is my project doing" in one call. It returns
`{project, summary, vocabulary, contains, url}`: the record, then the four
figures the Logs page shows as cards (trace count, error rate, average
duration, total cost) for the last 7 days against the 7 before, SDK traffic
only, as on screen. `since` / `until` move that window; `since="30d"` is what
the UI opens on. A rate or an average over a period with no traces comes back
as `null`, because 0% errors on a week with no traffic reads as a healthy week.

`vocabulary` is the map you need before you can ask anything else: the
project's feedback score names, its token usage keys, and the automation rules
scoring its traces. These are the names that go into a filter or into
`series=` below, and guessing them returns an empty page that reads like good
news. Score names and rules are capped, always report the true total, and name
the call that returns the rest; usage keys are listed in full, since nothing
else enumerates them. `contains` names the freshest experiment, dataset, prompt
version and optimization run, so "what has been happening here" does not need
four more calls. A part that failed to load says so instead of looking empty,
and an empty one is omitted.

An `agent_insights_issue` read returns `{issue, example_trace_ids, details}`:
the Diagnostics issue record (name, description, cause, suggested fix,
severity, status), the deduplicated ids of the traces that exhibit it (the
same sample the Diagnostics page shows — open one with `read("trace", id)`),
and the per-day breakdown. Trace bodies are not inlined, so the read stays one
backend call. `since` / `until` narrow the per-day rows; the default is
all-time. When the server knows the Opik URL and the session's workspace, the
read also carries `url` (the issue's Diagnostics page) and `trace_url_template`
(a deep link for any of the example traces), so the assistant can hand you
something clickable; under an OAuth session whose workspace could not be
resolved the links are omitted rather than guessed.

Traces themselves carry no URL — a link for one is not derivable from the
fields a `read` or `list` returns, and a guessed shape 404s. The session
instructions name a template for it instead,
`.../v1/session/redirect/projects/?trace_id={trace_id}&path=...`, so the
assistant fills in an id and hands you a link. It goes through opik-backend's
redirect, which resolves the project and the workspace from the trace, so it
works where a direct project URL cannot, an OAuth session with an unresolved
workspace included. It is the same link the Python SDK prints for a trace.

### `list`

Browse or search a collection with pagination. Project-scoped types (`trace`,
`span`, `thread`, `agent_insights_issue`, `dataset_item`, `prompt_version`)
need their parent: a project UUID or name, a dataset UUID, or a prompt UUID.

```python
list(entity_type="experiment", page=1, size=25)
list(entity_type="experiment", name="rerank")  # name substring filter
list(entity_type="agent_insights_issue", project_name="demo")  # open Diagnostics issues
list(entity_type="agent_insights_issue", project_id="<uuid>", status="resolved")
list(entity_type="trace", project_name="demo")  # latest traces of one project
list(
    entity_type="trace", project_name="demo", filters="error_info is_not_empty AND duration > 5000"
)
list(
    entity_type="span",
    project_name="demo",  # spans across the whole project
    filters='type = "llm" AND usage.total_tokens > 10000',
)
list(
    entity_type="thread",
    project_name="demo",
    filters="number_of_messages > 20 AND feedback_scores.helpfulness < 0.5",
)
list(entity_type="experiment", filters='dataset_id = "<dataset-uuid>" AND tags contains "baseline"')
```

**Filters.** `trace`, `span`, `thread`, `experiment` and `dataset_item` take an
OQL string, the same grammar as the SDK's `search_traces(filter_string=…)`:

```
<field>[.<key>] <op> <value> [AND ...]
ops: = != > >= < <= contains not_contains starts_with ends_with is_empty is_not_empty in not_in
```

Strings go in double quotes, numbers are bare, `duration` is in milliseconds,
dates are ISO-8601 instants with a timezone (`"2026-09-08T10:00:00Z"`).
Scores and dictionaries take a key: `feedback_scores.accuracy < 0.5`,
`metadata.environment = "prod"`. `AND` is the only connector.

Like the UI's Logs page, trace, span and thread lists add `source = "sdk"` so
evaluator, playground and experiment traces stay out of the way; name `source`
yourself to see them. The first output line echoes the filter that was applied.

A bad filter fails before reaching the backend with what is needed to fix it:
the position of a syntax error, the closest field name, the valid operators for
the field's type, or the expected value format. Fields with a closed set of
values (`source`, span `type`, thread `status`, `visibility_mode`) are checked
against it too, every element of an `in` list included. `source` is the one the
backend validates itself, and it answers an unknown value with a 500 rather
than a 400, so `source = "SDK"` would otherwise be an opaque server error for a
capital letter. The rest are compared as strings and answer with an empty page,
which reads as "no matches" when it means "no such value". Ask
`schema("list.trace")` (or `list.span`, `list.thread`, `list.experiment`) for
the full field reference, accepted values included.

**Finding one case in a dataset.** `list(entity_type="dataset_item",
dataset_id=…)` filters on the case itself: `data.<key>` for the keys the
dataset was built with, `full_data` for a substring of the whole payload (a
full scan — name a key when you can), plus `id`, `tags`, `source`, `trace_id`,
`span_id` and the timestamps. `data.<key>` takes the six string operators only
(`=`, `!=`, `contains`, `not_contains`, `starts_with`, `ends_with`); the
backend answers a comparison with a 400, so this one is refused before the
call. The endpoint has no sorting and no free-text search — `sort` is refused
rather than dropped. `read(entity_type="dataset_item", id=…)` returns one case
whole, which is how a value the table cut is read back.

```python
list(entity_type="dataset_item", dataset_id="<uuid>", filters='data.question contains "install"')
list(
    entity_type="dataset_item", dataset_id="<uuid>", filters='trace_id = "<trace-uuid>"'
)  # the case made from that trace
read(entity_type="dataset_item", id="<item-uuid>")  # the case, uncut
```

With `experiment_ids` the same list is the comparison instead — the cases with
each run attached — and it filters on the runs (`feedback_scores.<name>`,
`output`, `duration`). The two are different field sets on two backend
endpoints: `schema("list.dataset_item_case")` is the dataset's own cases,
`schema("list.dataset_item")` the comparison.

**Sort.** `trace`, `span`, `thread` and `experiment` take
`sort="<field> [asc|desc]"`, `desc` by default and one field only:
`sort="duration desc"`, `sort="total_estimated_cost"`,
`sort="feedback_scores.accuracy asc"`, `sort="usage.total_tokens"`. The field is
checked against the entity's sortable list before the call, because the backend
silently ignores fields it cannot sort by. On very large workspaces the backend
drops sorting altogether; the header says so when that happens.

`dataset_item` sorts only as a comparison (with `experiment_ids`): the items
endpoint takes no sorting parameter, so a sort on a plain listing is refused
rather than dropped.

**Time window and search.** `trace`, `span` and `thread` take `since` and
`until`, each a relative span (`"30m"`, `"1h"`, `"7d"`) or an ISO-8601 instant
with a timezone, so "the last hour" needs no clock arithmetic. The window is by
record creation time, which is cheap for the backend and agrees with
`start_time` within seconds for live traffic. For an exact bound, put
`start_time` in `filters`. The same three types take `search`, free text matched
anywhere in id, name, input, output, metadata, tags and thread id. Search scans
the whole project on the backend, so the first call on a large project can take
tens of seconds. Those calls get a 60-second timeout. Adding `since` makes them
fast again.

**Reading the table.** Durations are labelled `duration_ms` / `ttft_ms` and
shown as whole milliseconds; the field stays `duration` in `filters` and
`sort`. Timestamps are shown to the second and costs as plain decimals.
Project rows carry `last_updated_trace_at` so you can see which project has
live traffic; thread rows carry the first message. An empty page under a time
window says when the project's last trace landed, and an empty page under the
default `source = "sdk"` says how to see the other sources. A misspelled
`project_name` comes back with the closest existing name.

```python
list(
    entity_type="trace",
    project_name="demo",
    since="1h",
    filters="error_info is_not_empty",
    sort="duration desc",
)
list(entity_type="trace", project_name="demo", search="order-42")
```

**Diagnostics issues.** `agent_insights_issue` is the Diagnostics page over
the MCP: the recurring failures Opik's Diagnostics job grouped for a project,
ranked as the UI ranks them (most recently seen first). Columns are `severity`,
`status`, `total_occurrences` (all-time sum), `latest_count` (the most recent
report day, the number the issue's own description refers to) and `last_seen`.
Open issues are listed by default; pass `status="resolved"` or `"closed"` for
the rest. `read` and `list` also answer to `issue`, which is what the UI calls
these; the long name is the one in the `entity_type` enum, so that one entity
does not appear there twice. Counts are all-time so they match the UI; the same `since` / `until`
as for traces narrow the window, truncated to UTC report days because
Diagnostics aggregates per day.

An empty list says why it is empty, because "nothing is broken" and "nobody
turned Diagnostics on" read the same otherwise. There are five states:
Diagnostics is unavailable on this deployment, not enabled for this project,
turned off, enabled but not scanned recently, or enabled and clean with the
time of the last scan. The ones you can act on name the call to make, and every
state links the project's Diagnostics page.

A non-empty list dates itself. The issues are whatever the last scan grouped,
so the reply ends with `Report covers data through <time>`, and when the window
you asked about runs past that, it names the uncovered tail and how to close
it: a trigger when a rescan reaches back far enough, otherwise raw traces with
the `since` it gives you. Ask for a week on a project scanned nightly and the
last day is missing from the grouped answer; this is what says so.

`write("agent_insights_job.enable", {"project_name": "demo"})` turns Diagnostics
on. It scans daily from then on, and calling it again is safe.
`write("agent_insights_job.trigger", …)` scans the last 24 hours now, without
waiting for the nightly run. Both take the permission that reading issues takes,
and both refuse where the deployment has no Diagnostics.

An issue moves through its lifecycle with
`write("agent_insights_issue.resolve", {"issue_id": "<uuid>", "project_name": "demo"})`
— dealt with — or `…close` for one not worth acting on, and `…reopen` to put
either back on the open list. All three take the same permission and answer
with a link to the view the issue moved to, since a resolved issue is no longer
on the default page. Whether a failure is fixed is a judgment call, so these
are for when you ask: the assistant has no business tidying the list while
triaging it.

**Metrics over time.** `project_metric` charts one metric for a project as a
table of time buckets: trace, span and thread counts, durations, error rates,
costs, token usage and feedback scores. It answers the question that follows
the overview, which is when something changed.

```python
list(entity_type="project_metric", project_name="demo", metric_type="trace_count")
list(
    entity_type="project_metric",
    project_name="demo",
    metric_type="trace_error_rate",
    since="14d",
    interval="daily",
)
list(
    entity_type="project_metric", project_name="demo", metric_type="span_count", breakdown="model"
)  # one column per model
list(
    entity_type="project_metric",
    project_name="demo",
    metric_type="span_duration",
    breakdown="model",
    series="p99",
)  # the p99 of each model
```

Rows are time buckets, not records, so `page`, `size` and `sort` are refused
rather than ignored. `interval` is `hourly`, `daily`, `weekly` or `total`;
left out, it follows the window the way the Metrics tab does — hourly up to 3
days, daily up to 30, weekly beyond — so a default chart is a few dozen rows
whatever the range, and an hourly month (721 rows) is something you ask for.
`since` / `until` take the same forms as everywhere else and default to the
last 7 days. `filters` uses the fields of whichever entity the metric is
about, so a span metric is filtered by span fields.

`breakdown` splits each bucket by `tags`, `name`, `error_info`, `error_type`,
`model`, `provider`, `span_type`, `guardrail_name` or `metadata.<key>`. Not
every metric accepts every one of those, and seven accept none at all; the tool
knows which and says so before calling the backend, naming a metric that does
answer the same question where one exists. Three families come back as several
series at once (a duration as p50/p90/p99, a feedback score per name, token
usage per key), and the backend charts one of them at a time when grouping, so
`series=` picks it: a percentile, a score name, or a usage key. Duration
defaults to `p50` and token usage to `total_tokens`, and whichever was used is
echoed on the first line.

Empty buckets are left out and counted underneath, so a quiet month is a few
rows instead of a column of zeros, and a rate over a bucket with no traces is
absent rather than reported as zero.

Ask `schema("list.project_metric")` for the metric table, the intervals and the
per-metric grouping matrix.

**A project's names.** `score_name` lists the feedback score names recorded in
a project and `online_rule` the automation rule evaluators configured on it,
which is where most of those names come from. Both are the same lists
`read("project", …)` carries, in full and paginated, for when the capped
version in the overview is not enough.

```python
list(entity_type="score_name", project_name="demo")
list(entity_type="online_rule", project_name="demo")
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
| `dataset.create` | Create a dataset — `type: "test_suite"` makes it an evaluation test suite. |
| `dataset_item.upsert` | Upsert items into a dataset (always the envelope shape). |
| `experiment.create` | Create an experiment scoped to a dataset. |
| `experiment_item.create` | Attach trace + dataset_item rows to an experiment. |
| `thread.close` | Close a thread (mark it inactive). Pass `thread_id` and the project. |
| `thread.open` | Reopen a closed thread. Pass `thread_id` and the project. |
| `agent_insights_job.enable` | Turn Diagnostics on for a project (daily scans, safe to repeat). |
| `agent_insights_job.trigger` | Run a Diagnostics scan now, over the last 24 hours. |
| `agent_insights_issue.resolve` | Mark a Diagnostics issue dealt with (ask the user first). |
| `agent_insights_issue.close` | Mark a Diagnostics issue not worth acting on (ask the user first). |
| `agent_insights_issue.reopen` | Put a resolved or closed Diagnostics issue back on the open list. |

```python
write(
    operation="score.create",
    data={
        "target": "trace",
        "target_id": "7f2e3c8a-…",
        "name": "helpfulness",
        "value": 0.9,
        "reason": "great recovery",
    },
)
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

The same tool answers `list.trace`, `list.span`, `list.thread` and
`list.experiment` with the `list` tool's reference for that entity: every
filterable field with its type and valid operators, the sortable fields, whether
a time window and free-text search apply, and two example filters.

```python
schema(operation="list.trace")
```

---

## Configuration

These configure the local server; every setting is an environment variable.
The hosted server on Opik Cloud takes none of them.

### Identity / endpoint

| Variable | Default | Notes |
|---|---|---|
| `OPIK_API_KEY` | — | API key, for a self-hosted Comet, or for Opik Cloud without the hosted server. Open-source Opik needs none unless the deployment adds authentication. |
| `OPIK_WORKSPACE` | _unset_ | Workspace name. On cloud with an API key, unset sends `default`, which resolves to your account's **default** workspace — set it explicitly if you work in a different one, or reads come from the wrong workspace silently. Leave unset over OAuth (the token carries it) and on local/OSS (`default` is the only workspace there). |
| `COMET_WORKSPACE` | — | Deprecated alias for `OPIK_WORKSPACE` (backward compat). `OPIK_WORKSPACE` wins if both are set. |
| `COMET_WORKSPACE_ID` | _unset_ | Optional workspace UUID. Stamped into analytics events when set, and takes precedence over the resolved one. Rarely needed — OAuth installs get the UUID from the token automatically. |
| `COMET_URL_OVERRIDE` | `https://www.comet.com` | Set to your self-hosted Comet host, or `https://dev.comet.com` for staging. |
| `OPIK_URL` | derived from `COMET_URL_OVERRIDE` + `/opik/api` | Set it for open-source Opik, which serves its API at `/api` (`http://localhost:5173/api` locally). On a Comet platform, override only if Opik lives on a different host/path than the Comet UI. |
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
| Opik Cloud | Nothing to run: [the hosted server](#opik-cloud-the-hosted-server) is this server over HTTP, run by Comet |
| MCP client and Opik on the same machine (local OSS install) | **stdio** (recommended — simplest, no port, no OAuth setup) |
| Local MCP client → self-hosted Opik | stdio with the env vars for the deployment, or HTTP with OAuth (`OPIK_MCP_AS_URL` pointing at the backend) |
| opik-mcp served behind the same edge as opik-backend | **HTTP** — bearers are validated by the backend per request |

Note for local OSS installs: the OSS backend does not authenticate requests,
so an HTTP opik-mcp in front of it is as open as the OSS REST API itself.
Keep the default `127.0.0.1` bind (and prefer stdio) on shared networks.

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

**Does the setup work?** Run the server with the env from its client config and
`--check`: `OPIK_URL=http://localhost:5173/api uvx opik-mcp --check`. It names
the workspace and the projects it sees, or says what to fix. The server's own
errors also say what to change, and link the docs page for it.

**`OPIK_API_KEY` isn't picked up** — the var isn't reaching the server
process. In Claude Code / Cursor / VS Code, env vars only apply when inside
the `env` block of the MCP server config, not your shell; Codex also forwards
the names listed in `env_vars`. Start a new session after editing, since
clients read the config when a session starts.

**Requests go to `/opik/api` on an open-source Opik** — `COMET_URL_OVERRIDE` is
for a self-hosted Comet platform. Open source serves its API at `/api`: set
`OPIK_URL=http://localhost:5173/api` (or `https://<host>/api`) instead. The
server warns about this at startup when the host is this machine, and `--check`
answers "No Opik API at …".

**Cursor call times out at 60s** — Cursor's known bug, not `opik-mcp`. Either
narrow the call (smaller `size`, a tighter window), or run the same operation
on Claude Code which has no hard cap.

**Server not showing, sign-in not opening, wrong workspace, `uvx` not found.**
These are covered in the [troubleshooting section of the docs](https://www.comet.com/docs/opik/mcp-server/faq#troubleshooting).
`opik mcp status` (from the same `uvx opik` CLI) lists every client that has the
server configured and whether its config has drifted.

---

## Development

```bash
git clone git@github.com:comet-ml/opik-mcp.git
cd opik-mcp
make install        # uv sync --locked --extra dev
make check          # lint + typecheck + test
make run-dev        # uvicorn with --reload + DEBUG logs
make inspect        # MCP Inspector against the running server
```

Common targets:

| Target | What it does |
|---|---|
| `make install` | `uv sync --locked --extra dev` |
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
├── legacy/typescript/   ← migration guide for the deprecated v2 TS server (source: tag `legacy-typescript-final`)
├── pyproject.toml
└── Makefile
```

---

## Get help

- [Open an issue](https://github.com/comet-ml/opik-mcp/issues) for bugs and feature requests
- [Opik docs](https://www.comet.com/docs/opik/) for SDK / backend documentation
- [Comet community Slack](https://chat.comet.com/) for questions

---

## License

Apache-2.0.

<!-- The MCP Registry accepts server.json only if the PyPI description has the next line. Keep it. -->
<!-- mcp-name: io.github.comet-ml/opik-mcp -->
