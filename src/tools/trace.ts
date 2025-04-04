import { makeApiRequest } from '../utils/api.js';
import { z } from 'zod';
import { loadConfig } from './../config.js';
import { logToFile } from '../utils/logging.js';
import {
  ProjectResponse,
  TraceResponse,
  SingleTraceResponse,
  TraceStatsResponse,
} from './../types.js';

const config = loadConfig();

export const loadTraceTools = (server: any) => {
  if (config.mcpEnableTraceTools) {
    server.tool(
      'list-traces',
      'Get a list of traces',
      {
        page: z.number().describe('Page number for pagination'),
        size: z.number().describe('Number of items per page'),
        projectId: z.string().optional().describe('Project ID to filter traces'),
        projectName: z.string().optional().describe('Project name to filter traces'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { page, size, projectId, projectName, workspaceName } = args;
        let url = `/v1/private/traces?page=${page}&size=${size}`;

        // Add project filtering - API requires either project_id or project_name
        if (projectId) {
          url += `&project_id=${projectId}`;
        } else if (projectName) {
          url += `&project_name=${encodeURIComponent(projectName)}`;
        } else {
          // If no project specified, we need to find one for the API to work
          const projectsResponse = await makeApiRequest<ProjectResponse>(
            `/v1/private/projects?page=1&size=1`,
            {},
            workspaceName
          );

          if (
            projectsResponse.data &&
            projectsResponse.data.content &&
            projectsResponse.data.content.length > 0
          ) {
            const firstProject = projectsResponse.data.content[0];
            url += `&project_id=${firstProject.id}`;
            logToFile(
              `No project specified, using first available: ${firstProject.name} (${firstProject.id})`
            );
          } else {
            return {
              content: [
                {
                  type: 'text',
                  text: 'Error: No project ID or name provided, and no projects found',
                },
              ],
            };
          }
        }

        const response = await makeApiRequest<TraceResponse>(url, {}, workspaceName);

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to fetch traces' }],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: `Found ${response.data.total} traces (showing page ${
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
      'get-trace-by-id',
      'Get a single trace by ID',
      {
        traceId: z.string().describe('ID of the trace to fetch'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { traceId, workspaceName } = args;
        const response = await makeApiRequest<SingleTraceResponse>(
          `/v1/private/traces/${traceId}`,
          {},
          workspaceName
        );

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to fetch trace' }],
          };
        }

        // Format the response for better readability
        const formattedResponse: any = { ...response.data };

        // Format input/output if they're large
        if (
          formattedResponse.input &&
          typeof formattedResponse.input === 'object' &&
          Object.keys(formattedResponse.input).length > 0
        ) {
          formattedResponse.input = JSON.stringify(formattedResponse.input, null, 2);
        }

        if (
          formattedResponse.output &&
          typeof formattedResponse.output === 'object' &&
          Object.keys(formattedResponse.output).length > 0
        ) {
          formattedResponse.output = JSON.stringify(formattedResponse.output, null, 2);
        }

        return {
          content: [
            {
              type: 'text',
              text: `Trace Details for ID: ${traceId}`,
            },
            {
              type: 'text',
              text: JSON.stringify(formattedResponse, null, 2),
            },
          ],
        };
      }
    );

    server.tool(
      'get-trace-stats',
      'Get statistics for traces',
      {
        projectId: z.string().optional().describe('Project ID to filter traces'),
        projectName: z.string().optional().describe('Project name to filter traces'),
        startDate: z.string().optional().describe('Start date in ISO format (YYYY-MM-DD)'),
        endDate: z.string().optional().describe('End date in ISO format (YYYY-MM-DD)'),
        workspaceName: z
          .string()
          .optional()
          .describe('Workspace name to use instead of the default'),
      },
      async (args: any) => {
        const { projectId, projectName, startDate, endDate, workspaceName } = args;
        let url = `/v1/private/traces/stats`;

        // Build query parameters
        const queryParams = [];

        // Add project filtering - API requires either project_id or project_name
        if (projectId) {
          queryParams.push(`project_id=${projectId}`);
        } else if (projectName) {
          queryParams.push(`project_name=${encodeURIComponent(projectName)}`);
        } else {
          // If no project specified, we need to find one for the API to work
          const projectsResponse = await makeApiRequest<ProjectResponse>(
            `/v1/private/projects?page=1&size=1`,
            {},
            workspaceName
          );

          if (
            projectsResponse.data &&
            projectsResponse.data.content &&
            projectsResponse.data.content.length > 0
          ) {
            const firstProject = projectsResponse.data.content[0];
            queryParams.push(`project_id=${firstProject.id}`);
            logToFile(
              `No project specified, using first available: ${firstProject.name} (${firstProject.id})`
            );
          } else {
            return {
              content: [
                {
                  type: 'text',
                  text: 'Error: No project ID or name provided, and no projects found',
                },
              ],
            };
          }
        }

        if (startDate) queryParams.push(`start_date=${startDate}`);
        if (endDate) queryParams.push(`end_date=${endDate}`);

        if (queryParams.length > 0) {
          url += `?${queryParams.join('&')}`;
        }

        const response = await makeApiRequest<TraceStatsResponse>(url, {}, workspaceName);

        if (!response.data) {
          return {
            content: [{ type: 'text', text: response.error || 'Failed to fetch trace statistics' }],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: `Trace Statistics:`,
            },
            {
              type: 'text',
              text: JSON.stringify(response.data, null, 2),
            },
          ],
        };
      }
    );
  }

  return server;
};
