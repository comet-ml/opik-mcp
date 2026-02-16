import { z } from 'zod';
import { makeApiRequest } from '../utils/api.js';
import { DatasetItemsResponse, DatasetResponse, SingleDatasetResponse } from '../types.js';

export const loadDatasetTools = (server: any) => {
  server.tool(
    'list-datasets',
    'List datasets with optional filtering',
    {
      page: z.number().optional().default(1).describe('Page number for pagination'),
      size: z.number().optional().default(10).describe('Number of datasets per page'),
      name: z.string().optional().describe('Optional dataset name filter'),
      workspaceName: z.string().optional().describe('Workspace name override'),
    },
    async (args: any) => {
      const { page = 1, size = 10, name, workspaceName } = args;
      let url = `/v1/private/datasets?page=${page}&size=${size}`;
      if (name) {
        url += `&name=${encodeURIComponent(name)}`;
      }

      const response = await makeApiRequest<DatasetResponse>(url, {}, workspaceName);
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

  server.tool(
    'get-dataset-by-id',
    'Get details for a specific dataset',
    {
      datasetId: z.string().describe('Dataset ID'),
      workspaceName: z.string().optional().describe('Workspace name override'),
    },
    async (args: any) => {
      const { datasetId, workspaceName } = args;
      const response = await makeApiRequest<SingleDatasetResponse>(
        `/v1/private/datasets/${datasetId}`,
        {},
        workspaceName
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

  server.tool(
    'create-dataset',
    'Create a dataset for evaluations and experiments',
    {
      name: z.string().min(1).describe('Dataset name'),
      description: z.string().optional().describe('Optional dataset description'),
      workspaceName: z.string().optional().describe('Workspace name override'),
    },
    async (args: any) => {
      const { name, description, workspaceName } = args;
      const response = await makeApiRequest<any>(
        '/v1/private/datasets',
        {
          method: 'POST',
          body: JSON.stringify({
            name,
            ...(description && { description }),
          }),
        },
        workspaceName
      );

      if (!response.data) {
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
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'delete-dataset',
    'Delete a dataset by ID',
    {
      datasetId: z.string().describe('Dataset ID'),
      workspaceName: z.string().optional().describe('Workspace name override'),
    },
    async (args: any) => {
      const { datasetId, workspaceName } = args;
      const response = await makeApiRequest<any>(
        `/v1/private/datasets/${datasetId}`,
        { method: 'DELETE' },
        workspaceName
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

  server.tool(
    'list-dataset-items',
    'List items belonging to a dataset',
    {
      datasetId: z.string().describe('Dataset ID'),
      page: z.number().optional().default(1).describe('Page number for pagination'),
      size: z.number().optional().default(25).describe('Number of items per page'),
      workspaceName: z.string().optional().describe('Workspace name override'),
    },
    async (args: any) => {
      const { datasetId, page = 1, size = 25, workspaceName } = args;
      const response = await makeApiRequest<DatasetItemsResponse>(
        `/v1/private/datasets/${datasetId}/items?page=${page}&size=${size}`,
        {},
        workspaceName
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

  server.tool(
    'create-dataset-item',
    'Create a dataset item with input/output payloads',
    {
      datasetId: z.string().describe('Dataset ID'),
      input: z.record(z.any()).describe('Input payload for the dataset item'),
      expectedOutput: z
        .record(z.any())
        .optional()
        .describe('Optional expected output/label payload'),
      metadata: z.record(z.any()).optional().describe('Optional metadata payload'),
      workspaceName: z.string().optional().describe('Workspace name override'),
    },
    async (args: any) => {
      const { datasetId, input, expectedOutput, metadata, workspaceName } = args;
      const response = await makeApiRequest<any>(
        '/v1/private/datasets/items',
        {
          method: 'POST',
          body: JSON.stringify({
            dataset_id: datasetId,
            input,
            ...(expectedOutput && { expected_output: expectedOutput }),
            ...(metadata && { metadata }),
          }),
        },
        workspaceName
      );

      if (!response.data) {
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
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'delete-dataset-item',
    'Delete a dataset item by ID',
    {
      itemId: z.string().describe('Dataset item ID'),
      workspaceName: z.string().optional().describe('Workspace name override'),
    },
    async (args: any) => {
      const { itemId, workspaceName } = args;
      const response = await makeApiRequest<any>(
        `/v1/private/datasets/items/${itemId}`,
        { method: 'DELETE' },
        workspaceName
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
