import { describe, expect, jest, test } from '@jest/globals';
import type { OpikConfig } from '../src/config.js';
import { loadOpikResources } from '../src/resources/opik-resources.js';

function baseConfig(enabledToolsets: OpikConfig['enabledToolsets']): OpikConfig {
  return {
    apiBaseUrl: 'https://www.comet.com/opik/api',
    workspaceName: 'default',
    apiKey: 'test-key',
    isSelfHosted: false,
    debugMode: false,
    transport: 'stdio',
    streamableHttpPort: 3001,
    streamableHttpHost: '127.0.0.1',
    streamableHttpLogPath: '/tmp/opik-mcp-streamable-http.log',
    mcpName: 'opik-manager',
    mcpVersion: '1.0.0',
    mcpLogging: false,
    mcpDefaultWorkspace: 'default',
    enabledToolsets,
  };
}

describe('loadOpikResources', () => {
  test('always registers workspace-info', () => {
    const server = {
      registerResource: jest.fn(),
      resource: jest.fn(),
    };

    loadOpikResources(server, baseConfig(['core']));

    const names = server.registerResource.mock.calls.map(call => call[0]);
    expect(names).toContain('workspace-info');
  });

  test('registers project and trace resources with core toolset', () => {
    const server = {
      registerResource: jest.fn(),
      resource: jest.fn(),
    };

    loadOpikResources(server, baseConfig(['core']));

    const names = server.registerResource.mock.calls.map(call => call[0]);
    expect(names).toContain('projects-list');
    expect(names).toContain('projects-page');
    expect(names).toContain('trace-by-id');
    expect(names).toContain('traces-by-project-page');
    expect(names).not.toContain('prompts-page');
    expect(names).not.toContain('datasets-page');
  });

  test('registers prompt and dataset resources when expert toolsets enabled', () => {
    const server = {
      registerResource: jest.fn(),
      resource: jest.fn(),
    };

    loadOpikResources(server, baseConfig(['expert-prompts', 'expert-datasets']));

    const names = server.registerResource.mock.calls.map(call => call[0]);
    expect(names).toContain('prompts-page');
    expect(names).toContain('prompt-latest');
    expect(names).toContain('prompt-commit');
    expect(names).toContain('datasets-page');
    expect(names).toContain('dataset-by-id');
    expect(names).toContain('dataset-items-page');
    expect(names).not.toContain('projects-page');
    expect(names).not.toContain('traces-by-project-page');
  });

  test('registers trace resources with expert trace actions toolset', () => {
    const server = {
      registerResource: jest.fn(),
      resource: jest.fn(),
    };

    loadOpikResources(server, baseConfig(['expert-trace-actions']));

    const names = server.registerResource.mock.calls.map(call => call[0]);
    expect(names).toContain('trace-by-id');
    expect(names).toContain('traces-by-project-page');
  });
});
