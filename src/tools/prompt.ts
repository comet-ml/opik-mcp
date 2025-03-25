import { makeApiRequest } from '../utils/api.js';
import { z } from 'zod';
import { loadConfig } from './../config.js';
import { PromptResponse, SinglePromptResponse } from './../types.js';

const config = loadConfig();

export const loadPromptTools = (server: any) => {
  // Conditionally enable tool categories based on configuration
  if (config.mcpEnablePromptTools) {
    // ----------- PROMPTS TOOLS -----------

    server.tool(
      'list-opik-prompts',
      'Get a list of Opik prompts',
      {
        page: z.number().describe('Page number for pagination'),
        size: z.number().describe('Number of items per page'),
      },
      async (args: any) => {
        const response = await makeApiRequest<PromptResponse>(
          `/v1/private/prompts?page=${args.page}&size=${args.size}`
        );

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to fetch prompts' }],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: `Found ${response.data.total} prompts (showing page ${
                response.data.page
              } of ${Math.ceil(response.data.total / response.data.size)})`,
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
      'save-opik-prompt',
      'Save a prompt in Opik',
      {
        name: z.string().describe('Name of the prompt'),
      },
      async (args: any) => {
        const { name } = args;
        const response = await makeApiRequest<void>(`/v1/private/prompts`, {
          method: 'POST',
          body: JSON.stringify({ name }),
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
      'save-opik-prompt-version',
      'Save a new version of a prompt in the Opik platform',
      {
        name: z.string().describe('Name of the original prompt'),
        template: z.string().describe('Template content for the prompt version'),
        commit_message: z.string().describe('Commit message for the prompt version'),
      },
      async (args: any) => {
        const { name, template, commit_message } = args;
        const response = await makeApiRequest<any>(`/v1/private/prompts/versions`, {
          method: 'POST',
          body: JSON.stringify({
            name,
            version: { template, change_description: commit_message },
          }),
        });

        return {
          content: [
            {
              type: 'text',
              text: response.data
                ? 'Successfully created prompt version'
                : `${response.error} ${JSON.stringify(args)}` || 'Failed to create prompt version',
            },
          ],
        };
      }
    );

    server.tool(
      'get-opik-prompt-by-id',
      'Get a single prompt by ID',
      {
        promptId: z.string().describe('ID of the prompt to fetch'),
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
      'update-opik-prompt',
      'Update a prompt in the Opik platform',
      {
        promptId: z.string().describe('ID of the prompt to update'),
        name: z.string().describe('New name for the prompt'),
      },
      async (args: any) => {
        const { promptId, name } = args;
        const response = await makeApiRequest<void>(`/v1/private/prompts/${promptId}`, {
          method: 'PUT',
          body: JSON.stringify({ name }),
          headers: {
            'Content-Type': 'application/json',
          },
        });

        return {
          content: [
            {
              type: 'text',
              text: !response.error
                ? 'Successfully updated prompt'
                : response.error || 'Failed to update prompt',
            },
          ],
        };
      }
    );

    server.tool(
      'delete-opik-prompt',
      'Delete a prompt in the Opik platform',
      {
        promptId: z.string().describe('ID of the prompt to delete'),
      },
      async (args: any) => {
        const { promptId } = args;
        const response = await makeApiRequest<void>(`/v1/private/prompts/${promptId}`, {
          method: 'DELETE',
        });

        return {
          content: [
            {
              type: 'text',
              text: !response.error
                ? 'Successfully deleted prompt'
                : response.error || 'Failed to delete prompt',
            },
          ],
        };
      }
    );
  }
  return server;
};
