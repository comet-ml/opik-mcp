# Migrating from `npx opik-mcp` to `uvx opik-mcp`

> **Installing the Opik MCP server for the first time?** You don't need this
> page. Follow the [README](https://github.com/comet-ml/opik-mcp#quick-start).

The TypeScript MCP server (npm `opik-mcp@2`) is **deprecated** and stops serving
requests on **2026-11-15**. The supported server is the Python one, published
on PyPI as `opik-mcp`. This page covers what changes for an existing install:
the launch command, the env vars, command-line flags and `~/.opik.config`.

## On Opik Cloud: switch to the hosted server

On Opik Cloud (`www.comet.com`) you can drop the local server and the API key.
Point your MCP client at `https://www.comet.com/opik/api/v1/mcp` and sign in in
the browser when it asks. The
[README](https://github.com/comet-ml/opik-mcp#opik-cloud-the-hosted-server) has
the command for each client. The rest of this page is for keeping a local
server.

## The launch command

In your MCP client config, replace:

```jsonc
{ "command": "npx", "args": ["-y", "opik-mcp"] }
```

with:

```jsonc
{ "command": "uvx", "args": ["opik-mcp"] }
```

`opik-mcp@latest`, which the npm notice shows, works too. It asks PyPI for the
newest version on every start, so the server starts more slowly.

If you don't have `uv` yet, install it once:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# or: winget install astral-sh.uv                  # Windows
```

## Env vars

| TypeScript | Python | Notes |
|---|---|---|
| `OPIK_API_KEY` | `OPIK_API_KEY` | Unchanged. |
| `OPIK_WORKSPACE_NAME` | `OPIK_WORKSPACE` | The segment after `comet.com/opik/` in your Opik URL. Leave it unset on open-source Opik. `COMET_WORKSPACE` still works as an alias. |
| `OPIK_API_BASE_URL` | `OPIK_URL` | The Opik REST API: `http://localhost:5173/api` for a local open-source Opik, `https://<host>/api` for one on a server. Leave it unset on Opik Cloud. On a self-hosted Comet platform, set `COMET_URL_OVERRIDE=https://<host>` instead; the server adds `/opik/api`. |
| `OPIK_SELF_HOSTED` | _(removed)_ | Worked out from the URL. |
| `DEBUG_MODE=true` | `OPIK_MCP_LOG_LEVEL=DEBUG` | Also `INFO`, `WARNING`, `ERROR`. |
| `TRANSPORT` | `OPIK_MCP_TRANSPORT` | `stdio` or `streamable-http`. |
| `STREAMABLE_HTTP_PORT` | `OPIK_MCP_PORT` | The default moved from 3001 to 8080. |
| `STREAMABLE_HTTP_HOST` | `OPIK_MCP_HOST` | |
| `OPIK_TOOLSETS` | _(removed)_ | The tool set is fixed; drop the variable. |
| `MCP_DEFAULT_WORKSPACE` | _(removed)_ | It set the TypeScript SDK's default project, not the workspace. The nearest setting is `OPIK_DEFAULT_PROJECT_NAME`. |
| `MCP_NAME`, `MCP_VERSION`, `MCP_PORT`, `MCP_LOGGING`, `STREAMABLE_HTTP_LOG_PATH`, `STREAMABLE_HTTP_ACCESS_LOG`, `STREAMABLE_HTTP_CORS_ORIGINS`, `STREAMABLE_HTTP_RATE_LIMIT_MAX`, `STREAMABLE_HTTP_RATE_LIMIT_WINDOW_MS`, `STREAMABLE_HTTP_REQUIRE_AUTH`, `STREAMABLE_HTTP_TRUST_WORKSPACE_HEADERS`, `STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH`, `REMOTE_TOKEN_WORKSPACE_MAP` | _(removed)_ | Drop them. The HTTP transport's settings are in the README's [Server / transport](https://github.com/comet-ml/opik-mcp#server--transport) table. |

## Command-line flags are not read

The Python server takes its settings from env vars only. A flag left in your
client config, such as `--apiKey`, is ignored, and the server starts without
that setting: usually a 401 on the first call. The server logs a warning that
names the flag, on stderr, where few clients show it. Move each flag
into the `env` block. The TypeScript server also took each flag in kebab case
(`--api-key`, `--api-url`, `--streamable-http-port`, …); those map the same
way.

| TypeScript flag | Python env var |
|---|---|
| `--apiKey`, `--key` | `OPIK_API_KEY` |
| `--apiUrl`, `--url` | `OPIK_URL` (see the table above) |
| `--workspace`, `--ws` | `OPIK_WORKSPACE` |
| `--debug` | `OPIK_MCP_LOG_LEVEL=DEBUG` |
| `--transport`, `-t` | `OPIK_MCP_TRANSPORT` |
| `--streamableHttpPort`, `--port`, `-p` | `OPIK_MCP_PORT` |
| `--streamableHttpHost` | `OPIK_MCP_HOST` |
| `--mcpDefaultWorkspace` | none: it set the TypeScript SDK's default project, not the workspace. The nearest setting is `OPIK_DEFAULT_PROJECT_NAME`. |
| `--selfHosted`, `--toolsets`, `--streamableHttpLogPath`, `--mcpName`, `--mcpVersion`, `--mcpPort`, `--mcpLogging` | none; drop them |

## `~/.opik.config` is not read

The TypeScript server fell back to the Opik SDK's `~/.opik.config` for the key,
URL and workspace. The Python server does not read it. If your install relied
on it, put those values in the client's `env` block, or run
`uvx opik mcp configure`, which reads the file and writes the client config for
you.

## Check the result

Clients read their MCP config when a session starts, so start a new one. The
client should list the tools `read`, `list`, `write`, `schema` and
`read_skill` instead of the older `get-trace`, `list-prompts` and so on. Ask
the assistant to *"list my Opik projects"*; it calls `list` and shows your
projects. The [README](https://github.com/comet-ml/opik-mcp#tools) describes
every tool.

## Timeline

- **2026-05-28** — Soft deprecation (npm `deprecate` label, banner, MCP
  `instructions` field). TS server still fully functional.
- **2026-07-23** — Loud deprecation (tool description suffixes,
  per-tool-call notices).
- **2026-10-15** — Final 30-day warning version.
- **2026-11-15** — TS server `opik-mcp@2.1.0` ships as a stub: prints the
  migration message and exits without serving requests.

After 2026-11-15 the npm package no longer works; use `uvx opik-mcp` or the
hosted server.

## Questions / problems

- File an issue at <https://github.com/comet-ml/opik-mcp/issues>.
- Sunset policy: [`DEPRECATED.md`](./DEPRECATED.md).
