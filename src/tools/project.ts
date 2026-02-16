import { z } from 'zod';
import { callSdk, getOpikApi, getRequestOptions } from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';

interface ProjectToolOptions {
  includeReadOps?: boolean;
  includeMutations?: boolean;
}

export const loadProjectTools = (server: any, options: ProjectToolOptions = {}) => {
  const { includeReadOps = true, includeMutations = true } = options;

  if (includeReadOps) {
    registerTool(
      server,
      'list-projects',
      'List projects in the current workspace. Use this first to select a project ID/name for traces and metrics.',
      {
        page: z.number().optional().default(1).describe('Page number for pagination'),
        size: z.number().optional().default(10).describe('Number of items per page'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { page, size, workspaceName } = args;
        const api = getOpikApi();
        const response = await callSdk<any>(() =>
          api.projects.findProjects({ page, size }, getRequestOptions(workspaceName))
        );

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to fetch projects' }],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: `Found ${response.data.total} projects (page ${response.data.page} of ${Math.ceil(response.data.total / response.data.size)})`,
            },
            {
              type: 'text',
              text: JSON.stringify(response.data.content, null, 2),
            },
          ],
        };
      }
    );
  }

  if (includeMutations) {
    registerTool(
      server,
      'create-project',
      'Create a project when you need a new logical container for traces and evaluations.',
      {
        name: z.string().min(1).describe('Name of the project'),
        description: z.string().optional().describe('Description of the project'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { name, description, workspaceName } = args;
        const api = getOpikApi();
        const response = await callSdk<any>(() =>
          api.projects.createProject(
            {
              name,
              ...(description && { description }),
            },
            getRequestOptions(workspaceName)
          )
        );

        return {
          content: [
            {
              type: 'text',
              text: response.error || 'Successfully created project',
            },
          ],
        };
      }
    );
  }

  return server;
};
