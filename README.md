# Opik MCP Server

A Model Context Protocol (MCP) implementation for the [Opik](https://github.com/comet-ml/opik) platform with support for multiple transport mechanisms.

## Overview

This server provides a unified interface for interacting with the Opik platform through the Model Context Protocol (MCP). It supports:

- Standard Input/Output (stdio) transport for local integration with IDE
- Server-Sent Events (SSE) transport for network-based communication to run as a standalone server
- Multiple simultaneous client connections (with SSE transport)

## Features

- **Prompts Management**: Create, list, update, and delete prompts
- **Projects/Workspaces Management**: Organize and manage projects
- **Traces**: Track and analyze trace data
- **Metrics**: Gather and query metrics data

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/opik-mcp.git
cd opik-mcp

# Install dependencies and build
npm install
npm run build
```

### Configuration

Create a `.env` file based on the example:

```bash
cp .env.example .env
# Edit .env with your specific configuration
```

### Starting the Server

```bash
# Start with stdio transport (default)
npm run start:stdio

# Start with SSE transport for network access
npm run start:sse
```

## Available Commands

The project includes a Makefile for common operations:

```bash
# Display all available commands
make help

# Run tests
make test

# Run transport-specific tests
make test-transport

# Start the server with SSE transport
make start-sse

# Start the server with stdio transport
make start-stdio
```

## Transport Options

### Standard Input/Output

Ideal for local integration where the client and server run on the same machine.

```bash
make start-stdio
```

### Server-Sent Events (SSE)

Enables remote access and multiple simultaneous clients over HTTP.

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

## License

ISC
