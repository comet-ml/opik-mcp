import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';
import { SSEServerTransport } from '../../src/transports/sse-transport.js';
import fetch from 'node-fetch';

// Mock the fs module
jest.mock('fs', () => ({
  appendFileSync: jest.fn(),
}));

describe('SSEServerTransport (Streamable HTTP hosting)', () => {
  let transport: SSEServerTransport;
  const testPort = 3999;

  beforeEach(() => {
    transport = new SSEServerTransport({ port: testPort });
  });

  afterEach(async () => {
    await transport.close();
  });

  test('initializes with explicit and default options', () => {
    expect(new SSEServerTransport({ port: 4000 })).toBeInstanceOf(SSEServerTransport);
    expect(new SSEServerTransport()).toBeInstanceOf(SSEServerTransport);
  });

  test('responds to health check', async () => {
    await transport.start();

    const response = await fetch(`http://localhost:${testPort}/health`);
    const data = (await response.json()) as { status: string };

    expect(response.status).toBe(200);
    expect(data.status).toBe('ok');
  });

  test('rejects unauthenticated MCP requests', async () => {
    await transport.start();

    const response = await fetch(`http://localhost:${testPort}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: '1',
        method: 'tools/list',
        params: {},
      }),
    });

    const data = (await response.json()) as { status: string; message: string };
    expect(response.status).toBe(401);
    expect(data.status).toBe('error');
  });

  test('returns 410 for legacy endpoints', async () => {
    await transport.start();

    const response = await fetch(`http://localhost:${testPort}/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': 'test-token',
      },
      body: JSON.stringify({}),
    });

    const data = (await response.json()) as { status: string; message: string };
    expect(response.status).toBe(410);
    expect(data.status).toBe('error');
    expect(data.message).toContain('/mcp');
  });
});
