# Server-Sent Events (SSE) Transport

This document provides detailed information about the Server-Sent Events (SSE) transport implementation for the Opik MCP server.

## Overview

The SSE transport implementation allows the Opik MCP server to be hosted remotely and accessed over HTTP, enabling integration with web clients and remote systems. It uses Server-Sent Events for real-time, one-way communication from the server to clients, and standard HTTP requests for client-to-server communication.

## Features

- HTTP-based communication
- Real-time event streaming using SSE
- Support for multiple concurrent clients
- Health check endpoint
- Secure communication options
- Configurable port and host

## Configuration

The SSE transport can be configured using the following options:

| Option | Description | Default Value |
|--------|-------------|---------------|
| `ssePort` | The port on which the SSE server will listen | `3001` |
| `sseHost` | The host address to bind the SSE server | `localhost` |
| `sseLogPath` | Path to the log file for SSE transport | `/tmp/opik-mcp-sse.log` |

These options can be configured through environment variables or command-line arguments as described in the [configuration documentation](./configuration.md).

## Usage

### Starting the server with SSE transport

To start the MCP server with SSE transport, use the following command:

```bash
npm start -- --transport=sse
```

Or using the Makefile:

```bash
make start TRANSPORT=sse
```

### Connecting to the server

Clients can connect to the SSE server using standard HTTP requests for sending commands and SSE for receiving responses.

#### Health Check

To verify the server is running, you can access the health check endpoint:

```bash
curl http://localhost:3001/health
```

A successful response will return:

```json
{
  "status": "ok"
}
```

#### Example Client

A basic HTML client implementation is provided in the `client/index.html` file. This client demonstrates how to connect to the SSE server and interact with the MCP protocol.

To use the client:

1. Start the MCP server with SSE transport
2. Open the `client/index.html` file in a web browser
3. The client will automatically connect to the SSE server and display available commands

### Implementation Details

The SSE transport implementation consists of the following components:

1. **HTTP Server**: An Express.js server that handles HTTP requests and serves the SSE endpoint
2. **SSE Handler**: Manages SSE connections and broadcasts messages to connected clients
3. **Connection Handler**: Processes incoming MCP messages and forwards them to the MCP server
4. **Message Formatting**: Converts MCP messages to/from JSON format for transmission

## Security Considerations

When exposing the MCP server over HTTP, consider the following security precautions:

- Use HTTPS in production environments
- Implement authentication for accessing the API
- Restrict access using firewalls or API gateways
- Do not expose sensitive data through the API

## Limitations

The current SSE transport implementation has the following limitations:

- One-way communication from server to client (SSE limitation)
- No built-in authentication mechanism
- Limited error handling for network-related issues

## Troubleshooting

Check the SSE log file at `/tmp/opik-mcp-sse.log` (or your configured log path) for detailed information about connections and errors.

Common issues:

- **Port conflicts**: If the port is already in use, the server will fail to start. Change the port using the `ssePort` configuration option.
- **Connection timeouts**: Long-running SSE connections may time out in certain environments. Consider implementing reconnection logic in clients.
- **CORS issues**: If accessing from a different domain, you may encounter CORS restrictions. The SSE transport includes CORS headers, but additional configuration may be needed in complex setups.
