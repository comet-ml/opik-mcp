# Migrating from `npx opik-mcp` to `uvx opik-mcp`

The TypeScript MCP server (npm `opik-mcp@2.0.x`) is **deprecated** and will
stop serving requests on **2026-11-15**. The supported implementation is the
Python server (PyPI `opik-mcp`), launched via `uvx`. This guide covers the
two things that change for users: the launch command and the env vars.

## TL;DR

In your MCP client config, replace:

```jsonc
{ "command": "npx", "args": ["-y", "opik-mcp"] }
```

with:

```jsonc
{ "command": "uvx", "args": ["opik-mcp@latest"] }
```

If you don't have `uv` yet, install it once:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# or: winget install astral-sh.uv                  # Windows
```

That's the whole client-side change. Everything below covers env var renames
for users with non-default configs.

## Env var rename map

| TypeScript (legacy)                       | Python (current)              | Notes |
|-------------------------------------------|-------------------------------|-------|
| `OPIK_API_KEY`                            | `OPIK_API_KEY` ✓               | Unchanged. |
| `OPIK_API_BASE_URL`                       | `OPIK_URL`                    | For Opik Cloud, leave unset. For self-hosted, set to the base URL of your install. |
| `OPIK_WORKSPACE_NAME`                     | `COMET_WORKSPACE`             | Aligns with the rest of the Comet SDK. |
| `OPIK_SELF_HOSTED`                        | _(removed)_                   | Detected from `OPIK_URL`; no separate flag needed. |
| `DEBUG_MODE=true`                         | `OPIK_MCP_LOG_LEVEL=DEBUG`    | Standard log-level levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `TRANSPORT`                               | `OPIK_MCP_TRANSPORT`          | Same values (`stdio`, `streamable-http`). |
| `STREAMABLE_HTTP_PORT`                    | `OPIK_MCP_PORT`               | |
| `STREAMABLE_HTTP_HOST`                    | `OPIK_MCP_HOST`               | |
| `OPIK_TOOLSETS`                           | _(removed — see below)_       | Tool surface is fixed in the Python server. |

## Tool surface

The TS server exposed many narrow tools grouped into toolsets (`core`,
`expert-prompts`, `expert-datasets`, `metrics`, ...). The Python server
consolidates everything into six tools driven by a JSON-Schema dispatcher:

| Python tool | What it does |
|---|---|
| `read`       | Fetch a single entity by id / name / URI (`trace`, `span`, `project`, `experiment`, `prompt`, `test_suite`). |
| `list`       | Page through a collection. |
| `write`      | Mutating operations (scores, comments, prompt versions, experiments, ...) — uses an `operation` discriminator. |
| `schema`     | Returns the JSON Schema + example payload for any `write` operation. |
| `ask_ollie`  | Investigative questions, cross-entity synthesis. Returns a `thread_id` for follow-ups. |
| `run_experiment` | Launches an experiment over a dataset. |

If you previously referenced toolset names in `OPIK_TOOLSETS`, you can drop
that variable — there's nothing equivalent to set.

## Verification

After updating your MCP client config and restarting the host:

1. Open the MCP tool palette in your host (Claude Desktop / Cursor / VS Code).
2. Confirm you see `read`, `list`, `write`, `schema`, `ask_ollie`,
   `run_experiment` instead of the older `get-trace`, `list-prompts`, etc.
3. Try one read: ask the assistant *"list the first 3 traces in
   `<your-project>`"* — it should call `list` and return JSON.

## Timeline

- **2026-05-28** — Soft deprecation (npm `deprecate` label, banner, MCP
  `instructions` field). TS server still fully functional.
- **2026-07-23** — Loud deprecation (tool description suffixes,
  per-tool-call notices).
- **2026-10-15** — Final 30-day warning version.
- **2026-11-15** — TS server `opik-mcp@2.1.0` ships as a stub: prints the
  migration message and exits without serving requests.

After 2026-11-15 you **must** be on `uvx opik-mcp@latest` for the integration
to work.

## Questions / problems

- File an issue at <https://github.com/comet-ml/opik-mcp/issues>.
- Sunset policy: [`DEPRECATED.md`](./DEPRECATED.md).
