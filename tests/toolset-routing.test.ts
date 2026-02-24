import { describe, expect, jest, test } from '@jest/globals';
import { loadProjectTools } from '../src/tools/project.js';
import { loadTraceTools } from '../src/tools/trace.js';

function createMockServer() {
  return {
    tool: jest.fn().mockReturnThis(),
  };
}

describe('project tool routing', () => {
  test('registers only read tool for core project mode', () => {
    const server = createMockServer();

    loadProjectTools(server, { includeReadOps: true, includeMutations: false });

    const names = server.tool.mock.calls.map(call => call[0]);
    expect(names).toEqual(['list-projects']);
  });

  test('registers only mutation tool for expert project mode', () => {
    const server = createMockServer();

    loadProjectTools(server, { includeReadOps: false, includeMutations: true });

    const names = server.tool.mock.calls.map(call => call[0]);
    expect(names).toEqual(['create-project']);
  });
});

describe('trace tool routing', () => {
  test('registers only core trace tools', () => {
    const server = createMockServer();

    loadTraceTools(server, { includeCoreTools: true, includeExpertActions: false });

    const names = server.tool.mock.calls.map(call => call[0]);
    expect(names).toEqual([
      'list-traces',
      'get-trace-by-id',
      'get-trace-stats',
      'get-trace-threads',
    ]);
  });

  test('registers only expert trace action tools', () => {
    const server = createMockServer();

    loadTraceTools(server, { includeCoreTools: false, includeExpertActions: true });

    const names = server.tool.mock.calls.map(call => call[0]);
    expect(names).toEqual(['search-traces', 'add-trace-feedback']);
  });
});
