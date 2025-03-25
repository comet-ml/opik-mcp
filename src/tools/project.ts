import { makeApiRequest } from '../utils/api.js';
import { z } from 'zod';
import { loadConfig } from './../config.js';
import { ProjectResponse, SingleProjectResponse } from './../types.js';

const config = loadConfig();

export const loadProjectTools = (server: any) => {
  if (config.mcpEnableProjectTools) {
    server.tool(
      'list-projects',
      'Get a list of projects/workspaces',
      {
        page: z.number().describe('Page number for pagination'),
        size: z.number().describe('Number of items per page'),
        sortBy: z.string().optional().describe('Sort projects by this field'),
        sortOrder: z.string().optional().describe('Sort order (asc or desc)'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { page, size, sortBy, sortOrder, workspaceName } = args;

        // Build query string
        let url = `/v1/private/projects?page=${page}&size=${size}`;
        if (sortBy) url += `&sort_by=${sortBy}`;
        if (sortOrder) url += `&sort_order=${sortOrder}`;

        const response = await makeApiRequest<ProjectResponse>(url, {}, workspaceName);

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to fetch projects' }],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: `Found ${response.data.total} projects (showing page ${
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
      'get-project-by-id',
      'Get a single project by ID',
      {
        projectId: z.string().describe('ID of the project to fetch'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { projectId, workspaceName } = args;

        const response = await makeApiRequest<SingleProjectResponse>(
          `/v1/private/projects/${projectId}`,
          {},
          workspaceName
        );

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to fetch project' }],
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
      'create-project',
      'Create a new project/workspace',
      {
        name: z.string().describe('Name of the project'),
        description: z.string().optional().describe('Description of the project'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { name, description, workspaceName } = args;
        const response = await makeApiRequest<void>(
          `/v1/private/projects`,
          {
            method: 'POST',
            body: JSON.stringify({ name, description }),
          },
          workspaceName
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

    server.tool(
      'update-project',
      'Update a project',
      {
        projectId: z.string().describe('ID of the project to update'),
        name: z.string().optional().describe('New project name'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
        description: z.string().optional().describe('New project description'),
      },
      async (args: any) => {
        const { projectId, name, description, workspaceName } = args;

        // Build update data
        const updateData: Record<string, any> = {};
        if (name !== undefined) updateData.name = name;
        if (description !== undefined) updateData.description = description;

        const response = await makeApiRequest<SingleProjectResponse>(
          `/v1/private/projects/${projectId}`,
          {
            method: 'PATCH',
            body: JSON.stringify(updateData),
          },
          workspaceName
        );

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to update project' }],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: 'Project successfully updated',
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
      'delete-project',
      'Delete a project',
      {
        projectId: z.string().describe('ID of the project to delete'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { projectId, workspaceName } = args;
        const response = await makeApiRequest<void>(
          `/v1/private/projects/${projectId}`,
          {
            method: 'DELETE',
          },
          workspaceName
        );

        return {
          content: [
            {
              type: 'text',
              text: !response.error
                ? 'Successfully deleted project'
                : response.error || 'Failed to delete project',
            },
          ],
        };
      }
    );
  }
  return server;
};
