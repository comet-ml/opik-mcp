import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';
import { StreamableHttpTransport } from '../../src/transports/streamable-http-transport.js';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import fetch from 'node-fetch';

jest.mock('fs', () => ({
  appendFileSync: jest.fn(),
}));

jest.setTimeout(30000);

describe('MCP Server with Streamable HTTP integration', () => {
  let transport: StreamableHttpTransport;
  let mcpServer: McpServer;
  const basePort = 4501;
  let portOffset = 0;
  let currentPort: number;

  beforeEach(async () => {
    currentPort = basePort + portOffset;
    portOffset += 1;

    transport = new StreamableHttpTransport({ port: currentPort });
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

    (mcpServer as any).tool('echo', 'Echo input text', {}, async () => ({
      content: [{ type: 'text', text: 'echo:ok' }],
    }));

    await transport.start();
    await mcpServer.connect(transport);
  });

  afterEach(async () => {
    await mcpServer.close();
    await transport.close();
  });

  test('accepts initialize over /mcp with auth', async () => {
    const response = await fetch(`http://localhost:${currentPort}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2024-11-05',
        'x-api-key': 'test-token',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '1',
        method: 'initialize',
        params: {
          protocolVersion: '2024-11-05',
          capabilities: {},
          clientInfo: { name: 'test-client', version: '1.0.0' },
        },
      }),
    });

    expect(response.status).toBe(200);
  });

  test('returns initialize payload over /mcp', async () => {
    const response = await fetch(`http://localhost:${currentPort}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2024-11-05',
        'x-api-key': 'test-token',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '1',
        method: 'initialize',
        params: {
          protocolVersion: '2024-11-05',
          capabilities: {},
          clientInfo: { name: 'test-client', version: '1.0.0' },
        },
      }),
    });

    const body = await response.text();
    expect(response.status).toBe(200);
    expect(body).toContain('serverInfo');
  });
});
