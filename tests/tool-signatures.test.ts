import { describe, expect, test } from '@jest/globals';
import { loadProjectTools } from '../src/tools/project.js';
import { loadTraceTools } from '../src/tools/trace.js';
import { loadPromptTools } from '../src/tools/prompt.js';

type RegisteredTool = {
  config: {
    inputSchema: Record<string, any>;
  };
  handler: (...args: any[]) => unknown;
};

function createRegisterToolServer() {
  const tools = new Map<string, RegisteredTool>();
  const server = {
    registerTool: (name: string, config: any, handler: any) => {
      tools.set(name, { config, handler });
    },
  };

  return { server, tools };
}

describe('Tool signature guards', () => {
  test('pagination schemas reject non-positive values', () => {
    const { server, tools } = createRegisterToolServer();
    loadProjectTools(server as any);

    const listProjects = tools.get('list-projects');
    expect(listProjects).toBeDefined();
    expect(() => listProjects?.config.inputSchema.page.parse(0)).toThrow();
    expect(() => listProjects?.config.inputSchema.size.parse(0)).toThrow();
    expect(listProjects?.config.inputSchema.page.parse(undefined)).toBe(1);
  });

  test('search-traces sortBy only accepts allowed fields', () => {
    const { server, tools } = createRegisterToolServer();
    loadTraceTools(server as any, { includeCoreTools: false, includeExpertActions: true });

    const searchTraces = tools.get('search-traces');
    expect(searchTraces).toBeDefined();
    expect(searchTraces?.config.inputSchema.sortBy.parse('created_at')).toBe('created_at');
    expect(() => searchTraces?.config.inputSchema.sortBy.parse('random_field')).toThrow();
  });

  test('save-prompt-version keeps both new and legacy change description args', () => {
    const { server, tools } = createRegisterToolServer();
    loadPromptTools(server as any);

    const savePromptVersion = tools.get('save-prompt-version');
    expect(savePromptVersion).toBeDefined();
    expect(savePromptVersion?.config.inputSchema.changeDescription).toBeDefined();
    expect(savePromptVersion?.config.inputSchema.change_description).toBeDefined();
  });
});
