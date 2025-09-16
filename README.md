<h1 align="center" style="border-bottom: none">
    <div>
        <a href="https://www.comet.com/site/products/opik/?from=llm&utm_source=opik&utm_medium=github&utm_content=header_img&utm_campaign=opik-mcp">
            <picture>
                <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/comet-ml/opik-mcp/refs/heads/main/docs/assets/logo-dark-mode.svg">
                <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/comet-ml/opik-mcp/refs/heads/main/docs/assets/logo-light-mode.svg">
                <img alt="Comet Opik logo" src="docs/assets/logo-light-mode.svg" width="200" />
            </picture>
        </a>
        <br>
        Opik MCP Server
    </div>
    (Model Context Protocol)<br>
</h1>

<p align="center">
A Model Context Protocol (MCP) implementation for the <a href="https://github.com/comet-ml/opik/">Opik platform</a> with support for multiple transport mechanisms, enabling seamless integration with IDEs and providing a unified interface for Opik's capabilities.
</p>

<div align="center">

[![License](https://img.shields.io/github/license/comet-ml/opik-mcp)](https://github.com/comet-ml/opik-mcp/blob/main/LICENSE)
[![Node.js Version](https://img.shields.io/badge/node-%3E%3D20.11.0-brightgreen)](https://nodejs.org/)
[![TypeScript](https://img.shields.io/badge/typescript-%5E5.8.2-blue)](https://www.typescriptlang.org/)
<img src="https://badge.mcpx.dev?status=on" title="MCP Enabled"/>
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.15411156.svg)](https://doi.org/10.5281/zenodo.15411156)

</div>

<p align="center">
    <a href="https://www.comet.com/site/products/opik/?from=llm&utm_source=opik&utm_medium=github&utm_content=website_button&utm_campaign=opik"><b>Website</b></a> •
    <a href="https://chat.comet.com"><b>Slack community</b></a> •
    <a href="https://x.com/Cometml"><b>Twitter</b></a> •
    <a href="https://www.comet.com/docs/opik/?from=llm&utm_source=opik&utm_medium=github&utm_content=docs_button&utm_campaign=opik"><b>Documentation</b></a>
</p>

<p align="center">
    <a href="https://glama.ai/mcp/servers/@comet-ml/opik-mcp" rel="nofollow" target="_blank">
      <img width="380" height="200" src="https://glama.ai/mcp/servers/@comet-ml/opik-mcp/badge" alt="Opik Server MCP server" />
    </a>
</p>

> **⚠️ Notice:** SSE (Server-Sent Events) transport support is currently experimental and untested. For production use, we recommend using the direct process execution approach shown in the IDE integration examples.

## 🚀 What is Opik MCP Server?

Opik MCP Server is an open-source implementation of the Model Context Protocol for the Opik platform. It provides a unified interface for interacting with Opik's capabilities, supporting multiple transport mechanisms for flexible integration into various environments.

<br>

You can use Opik MCP Server for:
* **IDE Integration:**
  * Seamlessly integrate with Cursor and other compatible IDEs
  * Provide direct access to Opik's capabilities from your development environment

* **Unified API Access:**
  * Access all Opik features through a standardized protocol
  * Leverage multiple transport options (stdio, SSE) for different integration scenarios

* **Platform Management:**
  * Manage prompts, projects, traces, and metrics through a consistent interface
  * Organize and monitor your LLM applications efficiently

## Features

- **Prompts Management**: Create, list, update, and delete prompts
- **Projects/Workspaces Management**: Organize and manage projects
- **Traces**: Track and analyze trace data
- **Metrics**: Gather and query metrics data

## Quick Start

### Installation

#### Cursor Integration

To integrate with Cursor IDE, open to the Cursor settings page and navigate
to the Features tab. If you scroll down to the MCP section you will see the
button `+ Add new MCP server` that will allow you to add the Opik MCP server.

Once the `New MCP server` modal is open, select `command` as the server type and
enter the command: `npx -y opik-mcp --apiKey YOUR_API_KEY`.

Alternatively, you can create a `.cursor/mcp.json` in your project and add:

```json
{
  "mcpServers": {
    "opik": {
      "command": "npx",
      "args": [
        "-y",
        "opik-mcp",
        "--apiKey",
        "YOUR_API_KEY"
      ]
    }
  }
}
```

Note: If you are using the Open-Source version of Opik, you will need to specify
the `apiBaseUrl` parameter as `http://localhost:5173/api`.

#### Windsurf Installation

To install the MCP server in Windsurf, you will need to open the Windsurf settings
and navigate to the MCP section. From there, click on `View raw config` and update
the configuration object to be:

```json
{
    "mcpServers": {
      "opik": {
        "command": "npx",
        "args": [
          "-y",
          "opik-mcp",
          "--apiKey",
          "YOUR_API_KEY"
        ]
      }
    }
  }
```

Note: If you are using the Open-Source version of Opik, you will need to specify
the `apiBaseUrl` parameter as `http://localhost:5173/api`.

#### Manual Installation
```bash
# Clone the repository
git clone https://github.com/comet-ml/opik-mcp.git
cd opik-mcp

# Install dependencies and build
npm install
npm run build
```

**Configuration**

Create a `.env` file based on the example:

```bash
cp .env.example .env
# Edit .env with your specific configuration
```

**Starting the Server**

```bash
# Start with stdio transport (default)
npm run start:stdio

# Start with SSE transport for network access (experimental)
npm run start:sse
```

## Transport Options

### Standard Input/Output

Ideal for local integration where the client and server run on the same machine.

```bash
make start-stdio
```

### Server-Sent Events (SSE)

Enables remote access and multiple simultaneous clients over HTTP. Note that this transport option is experimental.

```bash
make start-sse
```

For detailed information about the SSE transport, see [docs/sse-transport.md](docs/sse-transport.md).

## Development

### Testing

```bash
# Run all tests
npm test

# Run specific test suite
npm test -- tests/transports/sse-transport.test.ts
```

### Pre-commit Hooks

This project uses pre-commit hooks to ensure code quality:

```bash
# Run pre-commit checks manually
make precommit
```

## Documentation

- [SSE Transport](docs/sse-transport.md) - Details on using the SSE transport
- [API Reference](docs/api-reference.md) - Complete API documentation
- [Configuration](docs/configuration.md) - Advanced configuration options
- [IDE Integration](docs/ide-integration.md) - Integration with Cursor IDE

## Citation

If you use this project in your research, please cite it as follows:

```
Comet ML, Inc, Koc, V., & Boiko, Y. (2025). Opik MCP Server. Github. https://doi.org/10.5281/zenodo.15411156
```

Or use the following BibTeX entry:

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

You can also find citation information in the `CITATION.cff` file in this repository.

## License

Apache 2.0
