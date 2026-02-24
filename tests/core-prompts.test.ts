import { describe, expect, jest, test } from '@jest/globals';
import { loadCorePrompts } from '../src/prompts/core-prompts.js';

describe('core prompts', () => {
  test('registers expected prompts', () => {
    const server = {
      prompt: jest.fn().mockReturnThis(),
    };

    loadCorePrompts(server);

    const names = server.prompt.mock.calls.map(call => call[0]);
    expect(names).toEqual(['opik-triage-workflow', 'opik-dataset-maintenance']);
  });
});
