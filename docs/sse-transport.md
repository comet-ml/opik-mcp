# SSE Transport for Opik MCP Server

This document explains how to use the SSE (Server-Sent Events) transport for the Opik Model Context Protocol server, which allows remote connections to the MCP server over HTTP.

## What is the SSE Transport?

The SSE transport is an alternative to the default stdio transport that allows the Opik MCP server to:

1. Accept connections over HTTP instead of only through standard input/output
2. Support multiple simultaneous clients
3. Be accessed remotely over a network or the internet
4. Integrate with web applications through a simple REST API

## How to Start the Server with SSE Transport

You can start the server with the SSE transport using either of these methods:

### Using npm Scripts

```bash
# Start the server with SSE transport
npm run start:sse

# Start the server with SSE on a custom port
npm run start:sse -- --port 8080
```

### Using the CLI Directly

```bash
# Build the project first if needed
npm run build

# Start the server with SSE transport
node build/cli.js serve --transport sse --port 3001
```

## API Endpoints

When running with the SSE transport, the server exposes the following HTTP endpoints:

- `GET /health` - Health check endpoint that returns `{ "status": "ok" }` when the server is running
- `GET /events` - SSE endpoint that clients can connect to for receiving MCP responses
- `POST /send` - Endpoint for sending MCP messages to the server

## Using the Test Client

A simple HTML-based test client is included in the `client/index.html` file. To use it:

1. Start the MCP server with SSE transport enabled
2. Open the `client/index.html` file in a web browser
3. Click "Connect" to establish an SSE connection
4. Enter your MCP message in JSON format and click "Send Message"

## Protocol Notes

### 1. Connecting

To connect to the SSE endpoint, create an EventSource pointing to the `/events` endpoint:

```javascript
const eventSource = new EventSource('http://localhost:3001/events');

// Optional: provide a client ID
const clientId = 'my-client-123';
const eventSource = new EventSource(`http://localhost:3001/events?clientId=${clientId}`);
```

### 2. Sending Messages

To send a message to the MCP server, POST a JSON-RPC 2.0 message to the `/send` endpoint:

```javascript
fetch('http://localhost:3001/send', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    jsonrpc: '2.0',
    method: 'mcp__get_server_info',
    id: '1',
    params: {}
  })
});
```

### 3. Receiving Responses

Responses from the MCP server will be sent as SSE events. Listen for them like this:

```javascript
eventSource.onmessage = (event) => {
  const data = event.data;
  // Parse and handle the data
  const response = JSON.parse(data);
  console.log('Received response:', response);
};
```

## Security Considerations

By default, the SSE transport does not implement authentication or authorization. For production use, consider:

1. Running the server behind a reverse proxy like Nginx with proper authentication
2. Implementing token-based authentication for the `/send` and `/events` endpoints
3. Using HTTPS to encrypt communications
4. Restricting access to trusted networks or implementing CORS policies

## Troubleshooting

If you encounter issues with the SSE transport:

1. Check the server logs for error messages
2. Make sure your firewall allows connections to the specified port
3. Verify that there are no other services running on the same port
4. Check browser console logs for client-side errors

## Example Messages

Here are some example messages to test the MCP server:

```json
{
  "jsonrpc": "2.0",
  "method": "mcp__get_server_info",
  "id": "1",
  "params": {}
}

{
  "jsonrpc": "2.0",
  "method": "mcp__list_prompts",
  "id": "2",
  "params": {
    "page": 1,
    "size": 10
  }
}

{
  "jsonrpc": "2.0",
  "method": "mcp__get_opik_help",
  "id": "3",
  "params": {
    "topic": "general"
  }
}
```
