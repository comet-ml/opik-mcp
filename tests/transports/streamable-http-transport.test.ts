import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';
import { StreamableHttpTransport } from '../../src/transports/streamable-http-transport.js';
import fetch from 'node-fetch';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';

// Mock the fs module
jest.mock('fs', () => ({
  appendFileSync: jest.fn(),
}));

describe('StreamableHttpTransport', () => {
  let transport: StreamableHttpTransport;
  let mcpServer: McpServer;
  const basePort = 3999;
  let portOffset = 0;
  let currentPort: number;
  const originalRequireAuth = process.env.STREAMABLE_HTTP_REQUIRE_AUTH;
  const originalValidateAuth = process.env.STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH;

  beforeEach(() => {
    process.env.STREAMABLE_HTTP_REQUIRE_AUTH = 'true';
    process.env.STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH = 'false';
    currentPort = basePort + portOffset;
    portOffset += 1;
    transport = new StreamableHttpTransport({ port: currentPort, host: '127.0.0.1' });

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

    mcpServer.tool('get-server-info', 'Get server info for onboarding', {}, async () => ({
      content: [{ type: 'text', text: 'server-info-ok' }],
    }));
  });

  afterEach(async () => {
    await mcpServer.close();
    await transport.close();
    process.env.STREAMABLE_HTTP_REQUIRE_AUTH = originalRequireAuth;
    process.env.STREAMABLE_HTTP_VALIDATE_REMOTE_AUTH = originalValidateAuth;
  });

  test('initializes with explicit and default options', () => {
    expect(new StreamableHttpTransport({ port: 4000 })).toBeInstanceOf(StreamableHttpTransport);
    expect(new StreamableHttpTransport()).toBeInstanceOf(StreamableHttpTransport);
  });

  test('responds to health check', async () => {
    await transport.start();

    const response = await fetch(`http://127.0.0.1:${currentPort}/health`);
    const data = (await response.json()) as { status: string };

    expect(response.status).toBe(200);
    expect(data.status).toBe('ok');
  });

  test('rejects unauthenticated MCP requests', async () => {
    await transport.start();

    const response = await fetch(`http://127.0.0.1:${currentPort}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2024-11-05',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '1',
        method: 'tools/call',
        params: {
          name: 'create-project',
          arguments: {
            name: 'should-fail',
          },
        },
      }),
    });

    const data = (await response.json()) as { status: string; message: string };
    expect(response.status).toBe(401);
    expect(data.status).toBe('error');
  });

  test('allows onboarding-safe unauthenticated initialize and tools/list', async () => {
    await transport.start();
    await mcpServer.connect(transport);

    const initializeResponse = await fetch(`http://127.0.0.1:${currentPort}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2024-11-05',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '2',
        method: 'initialize',
        params: {
          protocolVersion: '2024-11-05',
          capabilities: {},
          clientInfo: { name: 'test-client', version: '1.0.0' },
        },
      }),
    });

    expect(initializeResponse.status).toBe(200);

    const initializeSession = initializeResponse.headers.get('mcp-session-id');

    expect(initializeSession).toBeTruthy();
    expect(initializeSession).toEqual(expect.any(String));

    const toolsListResponse = await fetch(`http://127.0.0.1:${currentPort}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2024-11-05',
        'mcp-session-id': initializeSession as string,
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '3',
        method: 'tools/list',
        params: {},
      }),
    });

    const toolsListBody = await toolsListResponse.text();
    expect(toolsListResponse.status).toBe(200);
    expect(toolsListBody).toContain('get-server-info');
  });

  test('serves oauth protected resource metadata without oauth auth-server metadata', async () => {
    await transport.start();

    const response = await fetch(
      `http://127.0.0.1:${currentPort}/.well-known/oauth-protected-resource`
    );
    const data = (await response.json()) as { resource: string; opik_auth_mode: string };

    expect(response.status).toBe(200);
    expect(data.resource).toContain('/mcp');
    expect(data.opik_auth_mode).toBe('api_key');

    const authzServerResponse = await fetch(
      `http://127.0.0.1:${currentPort}/.well-known/oauth-authorization-server`
    );
    expect(authzServerResponse.status).toBe(404);
  });
});
