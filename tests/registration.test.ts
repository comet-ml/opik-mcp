import { describe, expect, jest, test } from '@jest/globals';
import { registerResource, registerTool } from '../src/tools/registration.js';

describe('registration helpers', () => {
  test('registerTool prefers modern registerTool API when available', () => {
    const server = {
      registerTool: jest.fn(),
      tool: jest.fn(),
    };

    registerTool(server, 'test-tool', 'desc', {}, async () => ({
      content: [{ type: 'text', text: 'ok' }],
    }));

    expect(server.registerTool).toHaveBeenCalledTimes(1);
    expect(server.tool).not.toHaveBeenCalled();
  });

  test('registerTool falls back to legacy tool API when needed', () => {
    const server = {
      tool: jest.fn(),
    };

    registerTool(server, 'test-tool', 'desc', {}, async () => ({
      content: [{ type: 'text', text: 'ok' }],
    }));

    expect(server.tool).toHaveBeenCalledTimes(1);
  });

  test('registerResource prefers modern registerResource API when available', () => {
    const server = {
      registerResource: jest.fn(),
      resource: jest.fn(),
    };

    registerResource(server, 'test-resource', 'opik://test', 'desc', async () => ({
      contents: [{ uri: 'opik://test', text: 'ok' }],
    }));

    expect(server.registerResource).toHaveBeenCalledTimes(1);
    expect(server.resource).not.toHaveBeenCalled();
  });

  test('registerResource falls back to legacy resource API when needed', () => {
    const server = {
      resource: jest.fn(),
    };

    registerResource(server, 'test-resource', 'opik://test', 'desc', async () => ({
      contents: [{ uri: 'opik://test', text: 'ok' }],
    }));

    expect(server.resource).toHaveBeenCalledTimes(1);
  });
});
