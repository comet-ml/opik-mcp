import { describe, expect, test, jest } from '@jest/globals';
import { loadCapabilitiesTools } from '../src/tools/capabilities.js';
import { loadProjectTools } from '../src/tools/project.js';
import type { OpikConfig } from '../src/config.js';
import appConfig from '../src/config.js';

describe('tool registration auth requirements', () => {
  const originalApiKey = appConfig.apiKey;

  afterEach(() => {
    appConfig.apiKey = originalApiKey;
  });

  test('requires API key for data tools by default', async () => {
    appConfig.apiKey = '';

    const calls: any[] = [];
    const server = {
      registerTool: jest.fn((...args: any[]) => {
        calls.push(args);
      }),
    } as any;

    loadProjectTools(server, { includeReadOps: true, includeMutations: true });
    expect(calls.some(call => call[0] === 'list-projects')).toBe(true);

    const projectToolsCall = calls.find(call => call[0] === 'list-projects');
    const projectToolsHandler = projectToolsCall?.[2];
    const result = await projectToolsHandler({ page: 1, size: 10 }, {});

    expect(result).toHaveProperty('content');
    expect(result.content?.[0]?.text).toContain('This Opik MCP request requires an API key');
  });

  test('allows onboarding-safe tools when no API key', async () => {
    appConfig.apiKey = '';

    const calls: any[] = [];
    const server = {
      registerTool: jest.fn((...args: any[]) => {
        calls.push(args);
      }),
    } as any;

    const serverConfig = {
      apiBaseUrl: 'https://www.comet.com/opik/api',
      isSelfHosted: false,
      debugMode: false,
      mcpName: 'opik-mcp',
      mcpVersion: '0.1.3',
      mcpLogging: false,
      mcpDefaultWorkspace: 'default',
      workspaceName: 'default',
      transport: 'streamable-http',
      enabledToolsets: ['core'],
      hasApiKey: false,
      apiKey: '',
    } as OpikConfig;

    loadCapabilitiesTools(server, serverConfig);

    const safeToolCall = calls.find(call => call[0] === 'get-server-info');
    const safeToolHandler = safeToolCall?.[2];
    const safeToolResult = await safeToolHandler({});

    expect(safeToolResult).toHaveProperty('content');
    expect(safeToolResult.content?.[0]?.text).toContain(
      '"apiBaseUrl": "https://www.comet.com/opik/api"'
    );
    expect(safeToolResult.content?.[0]?.text).toContain('"transport": "streamable-http"');
  });
});
