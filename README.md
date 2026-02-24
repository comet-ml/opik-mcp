<h1 align="center" style="border-bottom: none">
  <div>
    <a href="https://www.comet.com/site/products/opik/?from=llm&utm_source=opik&utm_medium=github&utm_content=header_img&utm_campaign=opik-mcp">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/comet-ml/opik-mcp/refs/heads/main/docs/assets/logo-dark-mode.svg">
        <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/comet-ml/opik-mcp/refs/heads/main/docs/assets/logo-light-mode.svg">
        <img alt="Comet Opik logo" src="docs/assets/logo-light-mode.svg" width="200" />
      </picture>
    </a>
    <br />
    Opik MCP Server
  </div>
</h1>

<p align="center">
Model Context Protocol (MCP) server for <a href="https://github.com/comet-ml/opik/">Opik</a>, with both local stdio and remote streamable-http transports.
</p>

<div align="center">

[![License](https://img.shields.io/github/license/comet-ml/opik-mcp)](https://github.com/comet-ml/opik-mcp/blob/main/LICENSE)
[![Node.js Version](https://img.shields.io/badge/node-%3E%3D20.11.0-brightgreen)](https://nodejs.org/)
[![TypeScript](https://img.shields.io/badge/typescript-%5E5.8.2-blue)](https://www.typescriptlang.org/)
<img src="https://badge.mcpx.dev?status=on" title="MCP Enabled" />
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.15411156.svg)](https://doi.org/10.5281/zenodo.15411156)

</div>

<p align="center">
  <a href="https://www.comet.com/site/products/opik/?from=llm&utm_source=opik&utm_medium=github&utm_content=website_button&utm_campaign=opik"><b>Website</b></a> •
  <a href="https://chat.comet.com"><b>Slack community</b></a> •
  <a href="https://x.com/Cometml"><b>Twitter</b></a> •
  <a href="https://www.comet.com/docs/opik/?from=llm&utm_source=opik&utm_medium=github&utm_content=docs_button&utm_campaign=opik"><b>Documentation</b></a>
</p>

> [!IMPORTANT]
> This repository ships the MCP server implementation only. We do not currently provide a hosted remote MCP service for Opik.
> If you run `streamable-http` remotely, authentication is fail-closed by default.

## Why this server

Opik MCP Server gives MCP-compatible clients one interface for:

- Prompt lifecycle management
- Workspace, project, and trace exploration
- Metrics and dataset operations
- MCP resources and resource templates for metadata-aware flows

## Quickstart

### 1. Run with npx

```bash
# Opik Cloud
npx -y opik-mcp --apiKey YOUR_API_KEY
```

For self-hosted Opik, pass `--apiUrl` (for example `http://localhost:5173/api`) and use your local auth strategy.

### 2. Add to your MCP client

Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "opik": {
      "command": "npx",
      "args": ["-y", "opik-mcp", "--apiKey", "YOUR_API_KEY"]
    }
  }
}
```

VS Code / GitHub Copilot (`.vscode/mcp.json`):

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "opik-api-key",
      "description": "Opik API Key",
      "password": true
    }
  ],
  "servers": {
    "opik-mcp": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "opik-mcp", "--apiKey", "${input:opik-api-key}"]
    }
  }
}
```

Windsurf (raw config):

```json
{
  "mcpServers": {
    "opik": {
      "command": "npx",
      "args": ["-y", "opik-mcp", "--apiKey", "YOUR_API_KEY"]
    }
  }
}
```

More client-specific examples: [docs/ide-integration.md](docs/ide-integration.md)

## Run from source

```bash
git clone https://github.com/comet-ml/opik-mcp.git
cd opik-mcp
npm install
npm run build
```

Optional local config:

```bash
cp .env.example .env
```

Start the server:

```bash
npm run start:stdio
npm run start:http
```

## Transport modes

| Transport | Use case | Command |
| --- | --- | --- |
| `stdio` | Local MCP integration (same machine as client) | `npm run start:stdio` |
| `streamable-http` | Remote/self-hosted MCP endpoint (`/mcp`) | `npm run start:http` |

### Remote auth defaults (`streamable-http`)

- `Authorization: Bearer <OPIK_API_KEY>` or `x-api-key` is required by default.
- Workspace is resolved server-side (token map recommended); workspace headers are not trusted by default.
- In remote mode, request-context workspace takes precedence over tool `workspaceName`.
- Missing or invalid auth returns HTTP `401`.

Key environment flags:

- `STREAMABLE_HTTP_REQUIRE_AUTH` (default `true`)
- `STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH` (default `true`, except test env)
- `REMOTE_TOKEN_WORKSPACE_MAP` (JSON token-to-workspace map)
- `STREAMABLE_HTTP_TRUST_WORKSPACE_HEADERS` (default `false`)

Deep dive: [docs/streamable-http-transport.md](docs/streamable-http-transport.md)

## Toolsets

Toolsets let you narrow which capabilities are enabled:

- `core`
- `integration`
- `expert-prompts`
- `expert-datasets`
- `expert-trace-actions`
- `expert-project-actions`
- `metrics`
- `all` (enables all modern toolsets)

Configure via:

- CLI: `--toolsets all`
- Env: `OPIK_TOOLSETS=core,expert-prompts,metrics`

Details: [docs/configuration.md](docs/configuration.md)

## MCP resources and prompts

- `resources/list` exposes static URIs (for example `opik://workspace-info`)
- `resources/templates/list` exposes dynamic URI templates (for example `opik://projects/{page}/{size}`)
- `resources/read` supports static and templated URIs
- `prompts/list` and `prompts/get` expose workflow prompts

## Development

```bash
# Lint
npm run lint

# Test
npm test

# Build
npm run build

# Run precommit checks
make precommit
```

## Documentation

- [API Reference](docs/api-reference.md)
- [Configuration](docs/configuration.md)
- [IDE Integration](docs/ide-integration.md)
- [Streamable HTTP Transport](docs/streamable-http-transport.md)

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

## Citation

If you use this project in research, cite:

```
Comet ML, Inc, Koc, V., & Boiko, Y. (2025). Opik MCP Server. Github. https://doi.org/10.5281/zenodo.15411156
```

BibTeX:

```bibtex
@software{CometML_Opik_MCP_Server_2025,
  author = {{Comet ML, Inc} and Koc, V. and Boiko, Y.},
  title = {{Opik MCP Server}},
  year = {2025},
  publisher = {GitHub},
  url = {https://doi.org/10.5281/zenodo.15411156},
  doi = {10.5281/zenodo.15411156}
}
```

Citation metadata is also available in [CITATION.cff](CITATION.cff).

## License

Apache 2.0
