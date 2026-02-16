import { makeApiRequest } from '../utils/api.js';
import { z } from 'zod';
import { ProjectResponse } from './../types.js';
import { registerTool } from './registration.js';

export const loadProjectTools = (server: any) => {
  registerTool(
    server,
    'list-projects',
    'Get a list of projects with optional filtering',
    {
      page: z.number().optional().default(1).describe('Page number for pagination'),
      size: z.number().optional().default(10).describe('Number of items per page'),
      workspaceName: z.string().optional().describe('Workspace name to use instead of the default'),
    },
    async (args: any) => {
      const { page, size, workspaceName } = args;
      const url = `/v1/private/projects?page=${page}&size=${size}`;

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

  registerTool(
    server,
    'create-project',
    'Create a new project',
    {
      name: z.string().min(1).describe('Name of the project'),
      description: z.string().optional().describe('Description of the project'),
      workspaceName: z.string().optional().describe('Workspace name to use instead of the default'),
    },
    async (args: any) => {
      const { name, description, workspaceName } = args;
      const requestBody: any = { name };
      if (description) requestBody.description = description;

      const response = await makeApiRequest<any>(
        `/v1/private/projects`,
        {
          method: 'POST',
          body: JSON.stringify(requestBody),
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

  return server;
};
