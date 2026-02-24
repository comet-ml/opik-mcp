import { z } from 'zod';
import { callSdk, getOpikApi } from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';
import { pageSchema, sizeSchema } from './schema.js';

export const loadPromptTools = (server: any) => {
  registerTool(
    server,
    'get-prompts',
    'List prompts with optional name filtering.',
    {
      page: pageSchema,
      size: sizeSchema(10),
      name: z.string().optional().describe('Optional prompt name filter.'),
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
    'Create a prompt definition.',
    {
      name: z.string().min(1).describe('Prompt name.'),
      description: z.string().optional().describe('Optional prompt description.'),
      tags: z.array(z.string().min(1)).optional().describe('Optional prompt tags.'),
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
    'Get a prompt by ID.',
    {
      promptId: z.string().min(1).describe('Prompt ID.'),
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
    'Get a specific prompt version by name and optional commit.',
    {
      name: z.string().min(1).describe('Prompt name.'),
      commit: z.string().optional().describe('Optional commit/version identifier.'),
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
    'Delete a prompt by ID.',
    {
      promptId: z.string().min(1).describe('Prompt ID.'),
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
    'Create a new prompt version.',
    {
      name: z.string().min(1).describe('Prompt name.'),
      template: z.string().min(1).describe('Prompt template body.'),
      changeDescription: z
        .string()
        .optional()
        .describe('Optional summary of changes in this version.'),
      change_description: z.string().optional().describe('Deprecated alias for changeDescription.'),
      metadata: z.record(z.any()).optional().describe('Additional metadata for the prompt version'),
      type: z.enum(['mustache', 'jinja2']).optional().describe('Template format.'),
    },
    async (args: any) => {
      const { name, template, change_description, changeDescription, metadata, type } = args;
      const resolvedChangeDescription = changeDescription ?? change_description;
      const api = getOpikApi();
      const response = await callSdk<any>(() =>
        api.prompts.createPromptVersion({
          name,
          version: {
            template,
            ...(resolvedChangeDescription && { changeDescription: resolvedChangeDescription }),
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
