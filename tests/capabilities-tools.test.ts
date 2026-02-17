import { describe, test, expect, jest } from '@jest/globals';
import { loadCapabilitiesTools } from '../src/tools/capabilities.js';
import type { OpikConfig } from '../src/config.js';

function createConfig(): OpikConfig {
  return {
    apiBaseUrl: 'https://www.comet.com/opik/api',
    workspaceName: 'default',
    apiKey: 'test-key',
    isSelfHosted: false,
    debugMode: false,
    transport: 'stdio',
    streamableHttpPort: 3001,
    streamableHttpHost: 'localhost',
    streamableHttpLogPath: '/tmp/opik-mcp-streamable-http.log',
    mcpName: 'opik-manager',
    mcpVersion: '1.0.0',
    mcpPort: 3001,
    mcpLogging: false,
    mcpDefaultWorkspace: 'default',
    enabledToolsets: ['expert-prompts', 'expert-trace-actions'],
  };
}

describe('Capabilities tools', () => {
  test('registers expected capabilities tools', () => {
    const server = {
      tool: jest.fn().mockReturnThis(),
    };

    loadCapabilitiesTools(server, createConfig());

    const toolNames = server.tool.mock.calls.map(call => call[0]);
    expect(toolNames).toEqual([
      'get-server-info',
      'get-opik-help',
      'get-opik-examples',
      'get-opik-metrics-info',
      'get-opik-tracing-info',
    ]);
  });

  test('get-server-info returns enabled toolsets and capabilities', async () => {
    const server = {
      tool: jest.fn().mockReturnThis(),
    };

    loadCapabilitiesTools(server, createConfig());

    const serverInfoTool = server.tool.mock.calls.find(call => call[0] === 'get-server-info');
    expect(serverInfoTool).toBeDefined();

    const handler = serverInfoTool?.[3] as (args: Record<string, unknown>) => Promise<any>;
    const result = await handler({});

    const payload = JSON.parse(result.content[0].text);
    expect(payload.hasApiKey).toBe(true);
    expect(payload.enabledToolsets).toContain('expert-prompts');
    expect(payload.capabilities.prompts.available).toBe(true);
    expect(payload.capabilities.projects.available).toBe(false);
    expect(payload.capabilities.traces.available).toBe(true);
  });
});
