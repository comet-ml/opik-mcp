import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';
import { StreamableHttpTransport } from '../../src/transports/streamable-http-transport.js';
import fetch from 'node-fetch';

// Mock the fs module
jest.mock('fs', () => ({
  appendFileSync: jest.fn(),
}));

describe('StreamableHttpTransport', () => {
  let transport: StreamableHttpTransport;
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
  });

  afterEach(async () => {
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
        method: 'tools/list',
        params: {},
      }),
    });

    const data = (await response.json()) as { status: string; message: string };
    expect(response.status).toBe(403);
    expect(data.status).toBe('error');
  });
});
