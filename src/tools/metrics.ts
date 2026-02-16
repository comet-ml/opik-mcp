import { makeApiRequest } from '../utils/api.js';
import { z } from 'zod';
import { logToFile } from '../utils/logging.js';
import { ProjectResponse, MetricsResponse } from './../types.js';
import { registerTool } from './registration.js';

export const loadMetricTools = (server: any) => {
  registerTool(
    server,
    'get-metrics',
    'Get metrics data',
    {
      metricName: z.string().optional().describe('Optional metric name to filter'),
      projectId: z.string().optional().describe('Optional project ID to filter metrics'),
      projectName: z.string().optional().describe('Optional project name to filter metrics'),
      startDate: z.string().optional().describe('Start date in ISO format (YYYY-MM-DD)'),
      endDate: z.string().optional().describe('End date in ISO format (YYYY-MM-DD)'),
    },
    async (args: any) => {
      const { metricName, projectId, projectName, startDate, endDate } = args;
      let url = `/v1/private/metrics`;

      const queryParams = [];
      if (metricName) queryParams.push(`metric_name=${metricName}`);

      // Add project filtering - API requires either project_id or project_name
      if (projectId) {
        queryParams.push(`project_id=${projectId}`);
      } else if (projectName) {
        queryParams.push(`project_name=${encodeURIComponent(projectName)}`);
      } else {
        // If no project specified, we need to find one for the API to work
        const projectsResponse = await makeApiRequest<ProjectResponse>(
          `/v1/private/projects?page=1&size=1`
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

      const response = await makeApiRequest<MetricsResponse>(url);

      if (!response.data) {
        return {
          content: [{ type: 'text', text: response.error || 'Failed to fetch metrics' }],
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

  return server;
};
