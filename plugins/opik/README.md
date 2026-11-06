# Opik plugin

The [Opik](https://github.com/comet-ml/opik) plugin gives Claude the Opik agent
skills and connects it to the Opik MCP server, so it can instrument an app,
read and score traces, run evaluations and explain failures in your Opik
workspace.

## Install

In Claude Code:

```
/plugin install opik --marketplace comet-ml/opik-mcp
```

Then start a new session and sign in from `/mcp` → **Authenticate**.

## What it adds

- **Skills:** `opik` (the SDK reference) and `opik-instrument`,
  `opik-diagnose`, `opik-explain`, `opik-evaluate`, `opik-compare`,
  `opik-test`, `opik-verify`, `opik-online-eval`, `opik-optimize` and
  `opik-annotate`.
- **MCP server `opik-cloud`:** Comet's hosted server at
  `https://www.comet.com/opik/api/v1/mcp`, for Opik Cloud. You sign in once in
  the browser (OAuth); there is no API key to set. Its tools are `read`,
  `list`, `write`, `schema` and `read_skill`.

## Self-hosted Opik

The skills work with any Opik. For your own server:

1. Add it. For a self-hosted Comet that serves the MCP server:
   `claude mcp add --transport http opik-mcp https://<host>/opik/api/v1/mcp`.
   For any other Opik, run `uvx opik@latest mcp configure`.
2. In `/mcp`, disable `plugin:opik:opik-cloud`, the Cloud server.

## What it sends, and where

- The MCP server calls the Opik API at `www.comet.com` with your sign-in. It
  reads or changes only the Opik data that you ask Claude to work with.
- The hosted server records product usage events: which tool ran, whether it
  failed and how long it took, with your Comet login and workspace. These
  events never include argument values, filter values, free text, paths or
  trace content.
- The hosted server sends a crash report to Comet's error tracking when an
  unexpected error occurs. A report can include details of the request that
  failed.
- The skills can ask Claude to install the Opik SDK (`pip install opik` or
  `npm install opik`) and to run code that sends traces from your app to your
  Opik workspace.

Source, issues and the full documentation:
[github.com/comet-ml/opik-mcp](https://github.com/comet-ml/opik-mcp).
