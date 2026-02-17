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

  test('supports initialize, initialized, tools/list, and tools/call', async () => {
    const baseHeaders = {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      'MCP-Protocol-Version': '2024-11-05',
      'x-api-key': 'test-token',
    };

    const initializeResponse = await fetch(`http://127.0.0.1:${currentPort}/mcp`, {
      method: 'POST',
      headers: baseHeaders,
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

    expect(initializeResponse.status).toBe(200);

    const sessionId = initializeResponse.headers.get('mcp-session-id');
    expect(sessionId).toBeTruthy();

    const sessionHeaders = {
      ...baseHeaders,
      'mcp-session-id': sessionId as string,
    };

    const initializedResponse = await fetch(`http://127.0.0.1:${currentPort}/mcp`, {
      method: 'POST',
      headers: sessionHeaders,
      body: JSON.stringify({
        jsonrpc: '2.0',
        method: 'notifications/initialized',
        params: {},
      }),
    });
    expect(initializedResponse.status).toBe(202);

    const toolsListResponse = await fetch(`http://127.0.0.1:${currentPort}/mcp`, {
      method: 'POST',
      headers: sessionHeaders,
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '2',
        method: 'tools/list',
        params: {},
      }),
    });
    const toolsListBody = await toolsListResponse.text();
    expect(toolsListResponse.status).toBe(200);
    expect(toolsListBody).toContain('echo');

    const callResponse = await fetch(`http://127.0.0.1:${currentPort}/mcp`, {
      method: 'POST',
      headers: sessionHeaders,
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '3',
        method: 'tools/call',
        params: {
          name: 'echo',
          arguments: {},
        },
      }),
    });
    const callBody = await callResponse.text();
    expect(callResponse.status).toBe(200);
    expect(callBody).toContain('echo:ok');
  });
});
