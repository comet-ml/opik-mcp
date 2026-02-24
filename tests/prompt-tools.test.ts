import { describe, test, expect, jest } from '@jest/globals';
import { loadPromptTools } from '../src/tools/prompt.js';

describe('Prompt tools', () => {
  test('registers expected prompt tools including id-based CRUD', () => {
    const server = {
      tool: jest.fn().mockReturnThis(),
    };

    loadPromptTools(server);

    const toolNames = server.tool.mock.calls.map(call => call[0]);
    expect(toolNames).toEqual([
      'get-prompts',
      'create-prompt',
      'get-prompt-by-id',
      'get-prompt-version',
      'delete-prompt',
      'save-prompt-version',
    ]);
  });
});
