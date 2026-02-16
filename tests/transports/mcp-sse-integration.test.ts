import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';
import { SSEServerTransport } from '../../src/transports/sse-transport.js';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import fetch from 'node-fetch';
import { JSONRPCMessage } from '@modelcontextprotocol/sdk/types.js';

// Mock the fs module
jest.mock('fs', () => ({
  appendFileSync: jest.fn(),
}));

// Increase the timeout for integration tests
jest.setTimeout(30000);

// Define debug function
const debug = (message: string) => {
  console.log(`[DEBUG] ${message}`);
};

describe('MCP Server with SSE Transport Integration', () => {
  let transport: SSEServerTransport;
  let mcpServer: McpServer;
  const testPort = 4501; // Using a high port number to avoid conflicts

  // Use a different port for each test to avoid conflicts
  let currentPort: number;

  beforeEach(async () => {
    // Generate a unique port for each test
    currentPort = testPort + Math.floor(Math.random() * 100);
    debug(`Setting up test environment using port ${currentPort}`);

    transport = new SSEServerTransport({ port: currentPort });
    mcpServer = new McpServer(
      {
        name: 'test-server',
        version: '1.0.0',
      },
      {
        capabilities: {
          tools: {},
        },
      }
    );

    // Start the server with SSE transport
    debug('Starting SSE transport');
    await transport.start();
    debug('Transport started, connecting MCP server');
    await mcpServer.connect(transport);
    debug('MCP server connected to transport');
  });

  afterEach(async () => {
    debug('Cleaning up test environment');

    debug('Closing MCP server connection');
    try {
      await mcpServer.close();
      debug('MCP server closed successfully');
    } catch (err) {
      debug(`Error closing MCP server: ${err}`);
    }

    debug('Closing SSE transport');
    try {
      await transport.close();
      debug('Transport closed successfully');
    } catch (err) {
      debug(`Error closing transport: ${err}`);
    }
  });

  test('server health check should return status ok', async () => {
    debug(`Testing health check at http://localhost:${currentPort}/health`);
    const response = await fetch(`http://localhost:${currentPort}/health`);
    const data = (await response.json()) as { status: string };

    debug(`Health check response: ${JSON.stringify(data)}`);
    expect(response.status).toBe(200);
    expect(data.status).toBe('ok');
  });

  test('server should respond to basic MCP requests', async () => {
    // Create a test message for the server info method
    const testMessage: JSONRPCMessage = {
      jsonrpc: '2.0',
      id: 'test-request',
      method: 'mcp__get_server_info',
      params: {},
    };

    debug(`Sending MCP request to http://localhost:${currentPort}/send`);
    debug(`Request payload: ${JSON.stringify(testMessage)}`);

    // Send the message to the server
    const response = await fetch(`http://localhost:${currentPort}/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(testMessage),
    });

    // Verify the request was accepted
    const data = (await response.json()) as { status: string };
    debug(`Response status: ${response.status}, body: ${JSON.stringify(data)}`);

    expect(response.status).toBe(200);
    expect(data.status).toBe('success');
  });

  test('server should handle invalid requests gracefully', async () => {
    // Create an invalid message (missing required fields)
    const invalidMessage = {
      // Missing jsonrpc and id fields
      method: 'invalid_method',
    };

    // Send the message to the server
    const response = await fetch(`http://localhost:${currentPort}/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(invalidMessage),
    });

    // The server should accept the message but handle errors internally
    // The exact behavior might vary, but we should get a successful response
    expect(response.status).toBe(200);
  });
});
