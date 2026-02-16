import { makeApiRequest } from '../utils/api.js';
import { z } from 'zod';
import { PromptResponse, SinglePromptResponse } from './../types.js';

export const loadPromptTools = (server: any) => {
  server.tool(
    'get-prompts',
    'Get a list of prompts with optional filtering',
    {
      page: z.number().optional().default(1).describe('Page number for pagination'),
      size: z.number().optional().default(10).describe('Number of items per page'),
      name: z.string().optional().describe('Filter by prompt name'),
    },
    async (args: any) => {
      const { page, size, name } = args;
      let url = `/v1/private/prompts?page=${page}&size=${size}`;
      if (name) url += `&name=${encodeURIComponent(name)}`;

      const response = await makeApiRequest<PromptResponse>(url);

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch prompts' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Found ${response.data.total} prompts (page ${response.data.page} of ${Math.ceil(response.data.total / response.data.size)})`,
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
    'create-prompt',
    'Create a new prompt',
    {
      name: z.string().min(1).describe('Name of the prompt'),
      description: z.string().optional().describe('Description of the prompt'),
      tags: z.array(z.string()).optional().describe('List of tags for the prompt'),
    },
    async (args: any) => {
      const { name, description, tags } = args;
      const requestBody: any = { name };
      if (description) requestBody.description = description;
      if (tags) requestBody.tags = tags;

      const response = await makeApiRequest<any>(`/v1/private/prompts`, {
        method: 'POST',
        body: JSON.stringify(requestBody),
      });

      return {
        content: [
          {
            type: 'text',
            text: response.error || 'Successfully created prompt',
          },
        ],
      };
    }
  );

  server.tool(
    'get-prompt-by-id',
    'Retrieve a prompt by ID',
    {
      promptId: z.string().min(1).describe('Prompt ID'),
    },
    async (args: any) => {
      const { promptId } = args;
      const response = await makeApiRequest<SinglePromptResponse>(
        `/v1/private/prompts/${promptId}`
      );

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch prompt' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'get-prompt-version',
    'Retrieve a specific version of a prompt',
    {
      name: z.string().min(1).describe('Name of the prompt'),
      commit: z.string().optional().describe('Specific commit/version to retrieve'),
    },
    async (args: any) => {
      const { name, commit } = args;
      const requestBody: any = { name };
      if (commit) requestBody.commit = commit;

      const response = await makeApiRequest<any>(`/v1/private/prompts/versions/retrieve`, {
        method: 'POST',
        body: JSON.stringify(requestBody),
      });

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch prompt version' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    'delete-prompt',
    'Delete a prompt by ID',
    {
      promptId: z.string().min(1).describe('Prompt ID'),
    },
    async (args: any) => {
      const { promptId } = args;
      const response = await makeApiRequest<any>(`/v1/private/prompts/${promptId}`, {
        method: 'DELETE',
      });

      if (response.error) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to delete prompt' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: `Successfully deleted prompt ${promptId}`,
          },
        ],
      };
    }
  );

  server.tool(
    'save-prompt-version',
    'Save a new version of a prompt',
    {
      name: z.string().min(1).describe('Name of the prompt'),
      template: z.string().describe('Template content for the prompt version'),
      change_description: z.string().optional().describe('Description of changes in this version'),
      metadata: z.record(z.any()).optional().describe('Additional metadata for the prompt version'),
      type: z.enum(['mustache', 'jinja2']).optional().describe('Template type'),
    },
    async (args: any) => {
      const { name, template, change_description, metadata, type } = args;
      const version: any = { template };
      if (change_description) version.change_description = change_description;
      if (metadata) version.metadata = metadata;
      if (type) version.type = type;

      const response = await makeApiRequest<any>(`/v1/private/prompts/versions`, {
        method: 'POST',
        body: JSON.stringify({ name, version }),
      });

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to create prompt version' }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: 'Successfully created prompt version',
          },
          {
            type: 'text',
            text: JSON.stringify(response.data, null, 2),
          },
        ],
      };
    }
  );

  return server;
};
