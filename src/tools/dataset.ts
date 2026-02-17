import { z } from 'zod';
import { callSdk, getOpikApi, getRequestOptions } from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';
import { pageSchema, sizeSchema, workspaceNameSchema } from './schema.js';

export const loadDatasetTools = (server: any) => {
  registerTool(
    server,
    'list-datasets',
    'List datasets with optional name filtering.',
    {
      page: pageSchema,
      size: sizeSchema(10),
      name: z.string().optional().describe('Optional dataset name filter.'),
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { page = 1, size = 10, name, workspaceName } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.datasets.findDatasets({ page, size, name }, getRequestOptions(workspaceName))
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch datasets' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Found ${response.data.total} datasets (page ${response.data.page} of ${Math.ceil(response.data.total / response.data.size)})`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data.content, null, 2),
          },
        ],
      };
    }
  );

  registerTool(
    server,
    'get-dataset-by-id',
    'Get full details for a dataset by ID.',
    {
      datasetId: z.string().min(1).describe('Dataset ID.'),
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { datasetId, workspaceName } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.datasets.getDatasetById(datasetId, getRequestOptions(workspaceName))
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch dataset' }],
        };
      }

      return {
        content: [{ type: 'text', text: JSON.stringify(response.data, null, 2) }],
      };
    }
  );

  registerTool(
    server,
    'create-dataset',
    'Create a dataset for evaluations and experiments.',
    {
      name: z.string().min(1).describe('Dataset name.'),
      description: z.string().optional().describe('Optional dataset description.'),
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { name, description, workspaceName } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.datasets.createDataset(
          {
            name,
            ...(description && { description }),
          },
          getRequestOptions(workspaceName)
        )
      );

      if (response.error) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to create dataset' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Successfully created dataset: ${name}`,
          },
        ],
      };
    }
  );

  registerTool(
    server,
    'delete-dataset',
    'Delete a dataset by ID.',
    {
      datasetId: z.string().min(1).describe('Dataset ID.'),
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { datasetId, workspaceName } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.datasets.deleteDataset(datasetId, getRequestOptions(workspaceName))
      );

      if (response.error) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to delete dataset' }],
        };
      }

      return {
        content: [{ type: 'text', text: `Successfully deleted dataset ${datasetId}` }],
      };
    }
  );

  registerTool(
    server,
    'list-dataset-items',
    'List items in a dataset.',
    {
      datasetId: z.string().min(1).describe('Dataset ID.'),
      page: pageSchema,
      size: sizeSchema(25, 500),
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { datasetId, page = 1, size = 25, workspaceName } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.datasets.getDatasetItems(datasetId, { page, size }, getRequestOptions(workspaceName))
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch dataset items' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Found ${response.data.total} dataset items (page ${response.data.page} of ${Math.ceil(response.data.total / response.data.size)})`,
          },
          {
            type: 'text',
            text: JSON.stringify(response.data.content, null, 2),
          },
        ],
      };
    }
  );

  registerTool(
    server,
    'create-dataset-item',
    'Create one dataset item with input, expected output, and metadata.',
    {
      datasetId: z.string().min(1).describe('Dataset ID.'),
      input: z.record(z.any()).describe('Input payload.'),
      expectedOutput: z.record(z.any()).optional().describe('Optional expected output payload.'),
      metadata: z.record(z.any()).optional().describe('Optional metadata payload.'),
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { datasetId, input, expectedOutput, metadata, workspaceName } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.datasets.createOrUpdateDatasetItems(
          {
            datasetId,
            items: [
              {
                source: 'manual',
                data: {
                  input,
                  ...(expectedOutput && { expected_output: expectedOutput }),
                  ...(metadata && { metadata }),
                },
              },
            ],
          },
          getRequestOptions(workspaceName)
        )
      );

      if (response.error) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to create dataset item' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Successfully created dataset item in dataset ${datasetId}`,
          },
        ],
      };
    }
  );

  registerTool(
    server,
    'delete-dataset-item',
    'Delete a dataset item by ID.',
    {
      itemId: z.string().min(1).describe('Dataset item ID.'),
      workspaceName: workspaceNameSchema,
    },
    async (args: any) => {
      const { itemId, workspaceName } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.datasets.deleteDatasetItems(
          {
            itemIds: [itemId],
          },
          getRequestOptions(workspaceName)
        )
      );

      if (response.error) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to delete dataset item' }],
        };
      }

      return {
        content: [{ type: 'text', text: `Successfully deleted dataset item ${itemId}` }],
      };
    }
  );

  return server;
};
