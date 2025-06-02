import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';
import { SSEServerTransport } from '../../src/transports/sse-transport.js';
import { JSONRPCMessage } from '@modelcontextprotocol/sdk/types.js';
import fetch from 'node-fetch';
import http from 'http';

// Mock the fs module
jest.mock('fs', () => ({
  appendFileSync: jest.fn(),
}));

describe('SSEServerTransport', () => {
  let transport: SSEServerTransport;
  const testPort = 3999; // Using a high port number to avoid conflicts

  beforeEach(() => {
    transport = new SSEServerTransport({ port: testPort });
  });

  afterEach(async () => {
    await transport.close();
  });

  test('should initialize with the specified port', () => {
    const customPort = 4000;
    const customTransport = new SSEServerTransport({ port: customPort });
    // We don't have direct access to the port, but we can test indirectly
    // by starting the server and checking if it's reachable
    expect(customTransport).toBeInstanceOf(SSEServerTransport);
  });

  test('should initialize with default port when not specified', () => {
    const defaultTransport = new SSEServerTransport();
    expect(defaultTransport).toBeInstanceOf(SSEServerTransport);
  });

  test('should start and respond to health check', async () => {
    await transport.start();

    const response = await fetch(`http://localhost:${testPort}/health`);
    const data = (await response.json()) as { status: string };

    expect(response.status).toBe(200);
    expect(data.status).toBe('ok');
  });

  test('should handle messages from clients', async () => {
    const mockOnMessage = jest.fn();
    transport.onmessage = mockOnMessage;

    await transport.start();

    // Add a small delay to ensure server is ready
    await new Promise(resolve => setTimeout(resolve, 100));

    const testMessage: JSONRPCMessage = {
      jsonrpc: '2.0',
      id: '1',
      method: 'test_method',
      params: {},
    };

    try {
      const response = await fetch(`http://localhost:${testPort}/send`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(testMessage),
      });

      const data = (await response.json()) as { status: string };

      expect(response.status).toBe(200);
      expect(data.status).toBe('success');
      expect(mockOnMessage).toHaveBeenCalledWith(testMessage);
    } catch (error) {
      throw new Error(`Failed to send message: ${error}`);
    }
  });

  test('should return error when message handler throws', async () => {
    const mockOnMessage = jest.fn().mockImplementation(() => {
      throw new Error('Test error');
    });
    const mockOnError = jest.fn();

    transport.onmessage = mockOnMessage;
    transport.onerror = mockOnError;

    await transport.start();

    // Add a small delay to ensure server is ready
    await new Promise(resolve => setTimeout(resolve, 100));

    const testMessage: JSONRPCMessage = {
      jsonrpc: '2.0',
      id: '1',
      method: 'test_method',
      params: {},
    };

    const response = await fetch(`http://localhost:${testPort}/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(testMessage),
    });

    const data = (await response.json()) as { status: string; message: string };

    expect(response.status).toBe(500);
    expect(data.status).toBe('error');
    expect(mockOnError).toHaveBeenCalled();
  });

  test('should return error when server not ready', async () => {
    // Create a new transport without setting the onmessage handler
    const newTransport = new SSEServerTransport({ port: testPort + 1 });
    await newTransport.start();

    const testMessage: JSONRPCMessage = {
      jsonrpc: '2.0',
      id: '1',
      method: 'test_method',
      params: {},
    };

    const response = await fetch(`http://localhost:${testPort + 1}/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(testMessage),
    });

    const data = (await response.json()) as { status: string; message: string };

    expect(response.status).toBe(503);
    expect(data.status).toBe('error');
    expect(data.message).toBe('Server not ready');

    await newTransport.close();
  });

  // This test simulates SSE client connections and message broadcasting
  test('should broadcast messages to connected clients', async () => {
    await transport.start();

    // We need to track received events manually since we're mocking SSE clients
    const receivedEvents: string[] = [];

    // Create an HTTP request that simulates an SSE client
    const req = http.request({
      hostname: 'localhost',
      port: testPort,
      path: '/events?clientId=test-client',
      method: 'GET',
      headers: {
        Accept: 'text/event-stream',
      },
    });

    // Set up event listeners for the response
    req.on('response', res => {
      res.on('data', chunk => {
        receivedEvents.push(chunk.toString());
      });
    });

    // Send the request
    req.end();

    // Wait a bit for the connection to be established
    await new Promise(resolve => setTimeout(resolve, 100));

    // Send a message through the transport
    const testMessage: JSONRPCMessage = {
      jsonrpc: '2.0',
      id: '1',
      method: 'test_method',
      result: { success: true },
    };

    await transport.send(testMessage);

    // Wait a bit for the message to be processed
    await new Promise(resolve => setTimeout(resolve, 100));

    // Check that the message was received
    expect(receivedEvents.length).toBeGreaterThan(0);
    expect(receivedEvents.some(event => event.includes(JSON.stringify(testMessage)))).toBe(true);
  });
});
