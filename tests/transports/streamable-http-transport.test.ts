import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';
import { StreamableHttpTransport } from '../../src/transports/streamable-http-transport.js';
import fetch from 'node-fetch';

// Mock the fs module
jest.mock('fs', () => ({
  appendFileSync: jest.fn(),
}));

describe('StreamableHttpTransport', () => {
  let transport: StreamableHttpTransport;
  const testPort = 3999;

  beforeEach(() => {
    transport = new StreamableHttpTransport({ port: testPort });
  });

  afterEach(async () => {
    await transport.close();
  });

  test('initializes with explicit and default options', () => {
    expect(new StreamableHttpTransport({ port: 4000 })).toBeInstanceOf(StreamableHttpTransport);
    expect(new StreamableHttpTransport()).toBeInstanceOf(StreamableHttpTransport);
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
});
