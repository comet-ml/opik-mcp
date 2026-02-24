import { describe, test, expect, jest } from '@jest/globals';
import { loadDatasetTools } from '../src/tools/dataset.js';

describe('Dataset tools', () => {
  test('registers expected dataset tools', () => {
    const server = {
      tool: jest.fn().mockReturnThis(),
    };

    loadDatasetTools(server);

    const toolNames = server.tool.mock.calls.map(call => call[0]);
    expect(toolNames).toEqual([
      'list-datasets',
      'get-dataset-by-id',
      'create-dataset',
      'delete-dataset',
      'list-dataset-items',
      'create-dataset-item',
      'delete-dataset-item',
    ]);
  });
});
