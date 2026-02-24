import { z } from 'zod';
import { callSdk, getOpikApi, getRequestOptions } from '../utils/opik-sdk.js';
import { registerTool } from './registration.js';
import { pageSchema, sizeSchema, workspaceNameSchema } from './schema.js';

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
      'List projects in the active workspace to find IDs for traces and metrics operations.',
      {
        page: pageSchema,
        size: sizeSchema(10),
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'List Projects',
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      }
    );
  }

  if (includeMutations) {
    registerTool(
      server,
      'create-project',
      'Create a new project for traces, prompts, and evaluation runs.',
      {
        name: z.string().min(1).describe('Project name.'),
        description: z.string().optional().describe('Optional project description.'),
        workspaceName: workspaceNameSchema,
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
      },
      {
        title: 'Create Project',
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      }
    );
  }

  return server;
};
