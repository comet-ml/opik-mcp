import { z } from 'zod';
import { callSdk, getOpikApi } from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';

export const loadPromptTools = (server: any) => {
  registerTool(
    server,
    'get-prompts',
    'Get a list of prompts with optional filtering',
    {
      page: z.number().optional().default(1).describe('Page number for pagination'),
      size: z.number().optional().default(10).describe('Number of items per page'),
      name: z.string().optional().describe('Filter by prompt name'),
    },
    async (args: any) => {
      const { page, size, name } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() => api.prompts.getPrompts({ page, size, name }));

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

  registerTool(
    server,
    'create-prompt',
    'Create a new prompt',
    {
      name: z.string().min(1).describe('Name of the prompt'),
      description: z.string().optional().describe('Description of the prompt'),
      tags: z.array(z.string()).optional().describe('List of tags for the prompt'),
    },
    async (args: any) => {
      const { name, description, tags } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.prompts.createPrompt({
          name,
          ...(description && { description }),
          ...(tags && { metadata: { tags } }),
        })
      );

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

  registerTool(
    server,
    'get-prompt-by-id',
    'Retrieve a prompt by ID',
    {
      promptId: z.string().min(1).describe('Prompt ID'),
    },
    async (args: any) => {
      const { promptId } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() => api.prompts.getPromptById(promptId));

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

  registerTool(
    server,
    'get-prompt-version',
    'Retrieve a specific version of a prompt',
    {
      name: z.string().min(1).describe('Name of the prompt'),
      commit: z.string().optional().describe('Specific commit/version to retrieve'),
    },
    async (args: any) => {
      const { name, commit } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.prompts.retrievePromptVersion({
          name,
          ...(commit && { commit }),
        })
      );

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

  registerTool(
    server,
    'delete-prompt',
    'Delete a prompt by ID',
    {
      promptId: z.string().min(1).describe('Prompt ID'),
    },
    async (args: any) => {
      const { promptId } = args;
      const api = getOpikApi();
      const response = await callSdk<any>(() => api.prompts.deletePrompt(promptId));

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

  registerTool(
    server,
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
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.prompts.createPromptVersion({
          name,
          version: {
            template,
            ...(change_description && { changeDescription: change_description }),
            ...(metadata && { metadata }),
            ...(type && { type }),
          },
        })
      );

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
